"""Real offline installation, serving and conversion of the shipped reference plugins.

The test wheels contain the actual example package files, including the vendored
parser. Building them directly keeps this test independent of network access and
build-backend availability; Hatchling/folder builds are not exercised here.
"""
from __future__ import annotations

import base64
import hashlib
import importlib
import re
import shutil
import subprocess
import sys
from pathlib import Path
import zipfile

import pytest
from typer.testing import CliRunner

from alfrd import extensions as ext
from alfrd.extensions import installer
from test_studio import studio_app  # noqa: F401 - fixture

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" / "plugins"
EPS = b"""%!PS-Adobe-3.0 EPSF-3.0
%%BoundingBox: 0 0 72 36
newpath 0 0 moveto 72 36 lineto stroke
showpage
%%EOF
"""


def _example_wheel(folder: Path, plugin_id: str) -> Path:
    """Build a standard pure-Python wheel from the example's metadata and package."""
    example = EXAMPLES / f"alfrd-{plugin_id}"
    # Read the small fixed example metadata without requiring tomllib (Python
    # 3.11+) or adding a test dependency on the host's supported Python 3.10.
    config = (example / "pyproject.toml").read_text()

    def value(key):
        return re.search(rf'^{re.escape(key)} = "([^"]+)"$', config, re.MULTILINE)[1]

    project_name, version = value("name"), value("version")
    name = project_name.replace("-", "_")
    info = f"{name}-{version}.dist-info"
    files = {}
    packages = [re.search(r'^packages = \["([^"]+)"\]$', config, re.MULTILINE)[1]]
    for package in packages:
        for source in sorted((example / package).rglob("*")):
            if source.is_file() and "__pycache__" not in source.parts:
                files[source.relative_to(example).as_posix()] = source.read_bytes()
    assert re.search(r'^dependencies = \[\]$', config, re.MULTILINE), "Update test builder for any future dependencies"
    metadata = (f"Metadata-Version: 2.1\nName: {project_name}\nVersion: {version}\n"
                f"Requires-Python: {value('requires-python')}\n")
    files[f"{info}/METADATA"] = metadata.encode()
    files[f"{info}/WHEEL"] = b"Wheel-Version: 1.0\nGenerator: alfrd-example-tests\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    entry_points = f"[alfrd.plugins]\n{plugin_id} = {value(plugin_id)}\n"
    files[f"{info}/entry_points.txt"] = entry_points.encode()
    rows = []
    for path, data in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        rows.append(f"{path},sha256={digest},{len(data)}\n")
    files[f"{info}/RECORD"] = ("".join(rows) + f"{info}/RECORD,,\n").encode()
    folder.mkdir(parents=True, exist_ok=True)
    wheel = folder / f"{name}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, data in files.items():
            archive.writestr(path, data)
    return wheel


@pytest.fixture
def installed_examples(tmp_path, monkeypatch):
    from alfrd.cli import alfrd_cli

    folder = tmp_path / "wheels"
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "uv-cache"))
    monkeypatch.setenv("UV_OFFLINE", "1")
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    original = installer.install
    extra = ("--no-index", "--find-links", str(folder))
    monkeypatch.setattr(installer, "install", lambda spec, **kw: original(spec, extra=extra, **kw))
    for plugin_id in ("markdown", "ps2pdf"):
        wheel = _example_wheel(folder, plugin_id)
        result = CliRunner().invoke(alfrd_cli, ["plugin", "install", str(wheel), "--yes"])
        assert result.exit_code == 0, result.output
    records = {record.id: record for record in ext.load(force=True)}
    assert records["markdown"].status == "ok", records["markdown"].error
    expected = "ok" if shutil.which("gs") else "missing binary: gs"
    assert records["ps2pdf"].status == expected, records["ps2pdf"].error
    yield records
    for name in ("alfrd_markdown", "alfrd_ps2pdf"):
        sys.modules.pop(name, None)


def test_examples_install_list_serve_and_disable(installed_examples, studio_app):
    app, _service = studio_app
    client = app.test_client()
    data = client.get("/api/studio/plugins").get_json()
    rows = {row["id"]: row for row in data["plugins"]}
    assert {"markdown", "ps2pdf"} <= rows.keys()
    assert rows["markdown"]["active"] and rows["markdown"]["web"]["js"] == "/studio/plugins/markdown/index.js"
    script = client.get(rows["markdown"]["web"]["js"])
    assert script.status_code == 200 and script.mimetype == "text/javascript"
    assert script.headers["X-Content-Type-Options"] == "nosniff"
    assert b"api.sanitize(marked.parse" in script.data
    for asset in ("marked.esm.js", "LICENSE-marked.md", "index.css"):
        response = client.get(f"/studio/plugins/markdown/{asset}")
        assert response.status_code == 200, asset
    parser = client.get("/studio/plugins/markdown/marked.esm.js")
    assert b"marked v15.0.12" in parser.data
    token = client.get("/api/studio/session").get_json()["csrf_token"]
    off = client.post("/api/studio/plugins/markdown/disable", headers={"X-CSRF-Token": token})
    assert off.status_code == 200 and not off.get_json()["plugin"]["active"]
    assert client.get("/studio/plugins/markdown/index.js").status_code == 404
    assert client.get("/studio/plugins/markdown/marked.esm.js").status_code == 404


@pytest.mark.skipif(shutil.which("gs") is None, reason="Ghostscript is not installed")
def test_example_eps_route_real_worker_and_cache(installed_examples, studio_app, tmp_path, monkeypatch):
    from alfrd.extensions import convert

    app, _service = studio_app
    client = app.test_client()
    root = tmp_path / "consumer"
    root.mkdir()
    shutil.copy(ROOT / "examples" / "avica_0.3" / "alfrd.yaml", root / "alfrd.yaml")
    (root / "figure.eps").write_bytes(EPS)
    token = client.get("/api/studio/session").get_json()["csrf_token"]
    created = client.post("/api/projects/connect", json={"path": str(root)}, headers={"X-CSRF-Token": token})
    assert created.status_code == 201, created.get_json()
    name = created.get_json()["name"]
    url = f"/api/studio/projects/{name}/convert?path=figure.eps&to=pdf"
    response = client.get(url)
    assert response.status_code == 200, response.get_json()
    assert response.mimetype == "application/pdf" and response.data.startswith(b"%PDF-")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    # EPSCrop must preserve the bounding box instead of producing an A4/Letter page.
    assert b"/MediaBox [0 0 72 36]" in response.data
    cache = list((root / ".alfrd" / "cache" / "convert").iterdir())
    assert len(cache) == 1 and cache[0].suffix == ".pdf"

    def unexpected_worker(*args):
        pytest.fail("cache hit launched the converter worker again")

    monkeypatch.setattr(convert, "_worker_cmd", unexpected_worker)
    again = client.get(url)
    assert again.status_code == 200 and again.data == response.data
    off = client.post("/api/studio/plugins/ps2pdf/disable", headers={"X-CSRF-Token": token})
    assert off.status_code == 200 and off.get_json()["restart_required"]
    assert client.get(url).status_code == 404


@pytest.mark.parametrize("suffix,crop", [(".ps", False), (".eps", True), (".EPS", True)])
def test_ps2pdf_command_and_keyword_timeout(tmp_path, monkeypatch, suffix, crop):
    monkeypatch.syspath_prepend(str(EXAMPLES / "alfrd-ps2pdf"))
    module = importlib.import_module("alfrd_ps2pdf")
    source, dest = tmp_path / f"a space;$(ignored){suffix}", tmp_path / "output space.pdf"
    seen = {}

    def run(command, **kwargs):
        seen.update(command=command, kwargs=kwargs)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(module.subprocess, "run", run)
    module.ps_to_pdf(source, dest, timeout=3.5)
    assert seen["command"][:5] == ["gs", "-dSAFER", "-dBATCH", "-dNOPAUSE", "-sDEVICE=pdfwrite"]
    assert ("-dEPSCrop" in seen["command"]) == crop
    assert seen["command"][-3:] == [f"-sOutputFile={dest}", "-f", str(source)]
    assert seen["kwargs"] == {"capture_output": True, "text": True, "timeout": 3.5}
    sys.modules.pop("alfrd_ps2pdf", None)


def test_ps2pdf_failure_has_bounded_stderr(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(EXAMPLES / "alfrd-ps2pdf"))
    module = importlib.import_module("alfrd_ps2pdf")
    monkeypatch.setattr(module.subprocess, "run", lambda command, **kwargs:
                        subprocess.CompletedProcess(command, 1, "", "x" * 4000 + "END"))
    with pytest.raises(RuntimeError, match="Ghostscript exited with status 1") as error:
        module.ps_to_pdf(tmp_path / "bad.ps", tmp_path / "bad.pdf", timeout=1)
    assert str(error.value).endswith("END") and len(str(error.value)) < 2100
    sys.modules.pop("alfrd_ps2pdf", None)
