"""Plugin converter execution: worker process, cache, limits, errors, and the Studio route (T3.5)."""

from __future__ import annotations

import importlib.metadata
import shutil
import sys
import textwrap
import zipfile
from pathlib import Path

import pytest

from alfrd import extensions as ext
from alfrd.extensions import convert as conv
from plugin_wheel import build_wheel
from test_studio import studio_app  # noqa: F401 - fixture

PROJECT_ROOT = Path(__file__).resolve().parents[1]

COPY_PLUGIN = """
import shutil
from alfrd.extensions import Plugin, Converter

def run(src, dest, *, timeout):
    shutil.copyfile(src, dest)

plugin = Plugin(id="psconv", version="1.0", converters=[Converter(src=[".PS", ".eps"], to="pdf", run=run)])
"""

MISSING_PLUGIN = """
from alfrd.extensions import Plugin, Converter

def run(src, dest, *, timeout):
    raise AssertionError("never runs")

plugin = Plugin(id="needsgs", version="1", requires_bin=["alfrd-no-such-gs-xyz"],
                converters=[Converter(src=[".xps"], to="pdf", run=run)])
"""

# Worker stand-in: count the call, then copy SRC to DEST (argv: counter src dest).
COPY_WORKER = """
import shutil, sys
with open(sys.argv[1], "a") as f:
    f.write("x")
shutil.copyfile(sys.argv[2], sys.argv[3])
"""


@pytest.fixture
def fake_plugins(tmp_path, monkeypatch):
    """``add(name, source)`` writes module ``alfrd_fake_<name>`` and an entry point ``name`` for it."""
    mods = tmp_path / "fake_mods"
    mods.mkdir()
    monkeypatch.syspath_prepend(str(mods))
    eps: list = []
    monkeypatch.setattr(importlib.metadata, "entry_points",
                        lambda group=None, **kw: [e for e in eps if group in (None, e.group)])

    def add(name: str, source: str, attr: str = "plugin") -> None:
        module = f"alfrd_fake_{name.replace('-', '_')}"
        (mods / f"{module}.py").write_text(textwrap.dedent(source))
        sys.modules.pop(module, None)
        eps.append(importlib.metadata.EntryPoint(name, f"{module}:{attr}", ext.GROUP))

    yield add
    ext.reset()
    for name in list(sys.modules):
        if name.startswith("alfrd_fake_"):
            sys.modules.pop(name)


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    (root / "plots").mkdir(parents=True)
    (root / "plots" / "a.ps").write_bytes(b"%!PS-Adobe-3.0\nshowpage\n")
    return root


@pytest.fixture
def copy_worker(tmp_path, monkeypatch):
    """Replace the worker command with a counting copier; returns the counter file."""
    counter = tmp_path / "calls.txt"
    counter.write_text("")
    monkeypatch.setattr(conv, "_worker_cmd", lambda plugin_id, to, src, dest, timeout:
                        [sys.executable, "-c", COPY_WORKER, str(counter), str(src), str(dest)])
    return counter


def _cache_files(root: Path) -> list[Path]:
    folder = conv.cache_dir(root)
    return sorted(folder.iterdir()) if folder.is_dir() else []


def test_cache_miss_runs_worker_and_hit_does_not(fake_plugins, project, copy_worker):
    fake_plugins("psconv", COPY_PLUGIN)
    ext.load(force=True)
    out = conv.convert(project, "plots/a.ps", "pdf")
    assert out.read_bytes() == (project / "plots" / "a.ps").read_bytes()
    assert out.parent == project / ".alfrd" / "cache" / "convert" and out.suffix == ".pdf"
    assert copy_worker.read_text() == "x"
    again = conv.convert(project, "plots/a.ps", ".PDF")
    assert again == out and copy_worker.read_text() == "x"  # cached: no second run
    assert _cache_files(project) == [out]  # no .part leftovers
    rec, _ = conv.find_converter(".ps", "pdf")
    assert out == conv.cache_path(project.resolve(), project / "plots" / "a.ps", rec, "pdf")
    (project / "plots" / "a.ps").write_bytes(b"%!PS changed\n")
    assert conv.convert(project, "plots/a.ps", "pdf") != out and copy_worker.read_text() == "xx"


def test_timeout_kills_worker_and_leaves_no_file(fake_plugins, project, monkeypatch):
    fake_plugins("psconv", COPY_PLUGIN)
    ext.load(force=True)
    monkeypatch.setattr(conv, "GRACE", 0.0)
    monkeypatch.setattr(conv, "_worker_cmd", lambda plugin_id, to, src, dest, timeout:
                        [sys.executable, "-c", f"import time; open({str(dest)!r}, 'wb').write(b'partial'); time.sleep(60)"])
    with pytest.raises(conv.ConvertError) as info:
        conv.convert(project, "plots/a.ps", "pdf", timeout=0.5)
    assert info.value.status == 504
    assert _cache_files(project) == []


def test_worker_failure_and_empty_output_are_502(fake_plugins, project, monkeypatch):
    fake_plugins("psconv", COPY_PLUGIN)
    ext.load(force=True)
    monkeypatch.setattr(conv, "_worker_cmd", lambda *a: [sys.executable, "-c", "import sys; sys.stderr.write('boom ' * 1000 + 'END'); sys.exit(1)"])
    with pytest.raises(conv.ConvertError) as info:
        conv.convert(project, "plots/a.ps", "pdf")
    assert info.value.status == 502 and info.value.detail_tail.endswith("END") and len(info.value.detail_tail) <= 2000
    monkeypatch.setattr(conv, "_worker_cmd", lambda *a: [sys.executable, "-c", "pass"])
    with pytest.raises(conv.ConvertError) as info:
        conv.convert(project, "plots/a.ps", "pdf")
    assert info.value.status == 502 and "no output" in str(info.value)
    assert _cache_files(project) == []


def test_output_over_limit_is_413_and_deleted(fake_plugins, project, copy_worker, monkeypatch):
    fake_plugins("psconv", COPY_PLUGIN)
    ext.load(force=True)
    monkeypatch.setattr(conv, "MAX_OUTPUT", 4)
    with pytest.raises(conv.ConvertError) as info:
        conv.convert(project, "plots/a.ps", "pdf")
    assert info.value.status == 413 and _cache_files(project) == []


@pytest.mark.parametrize("rel, status", [
    ("../x.ps", 400), ("plots/../../x.ps", 400), ("/etc/passwd.ps", 400), ("", 400), ("plots/missing.ps", 404),
])
def test_bad_paths(fake_plugins, project, copy_worker, rel, status):
    fake_plugins("psconv", COPY_PLUGIN)
    ext.load(force=True)
    (project.parent / "x.ps").write_text("outside")
    with pytest.raises(conv.ConvertError) as info:
        conv.convert(project, rel, "pdf")
    assert info.value.status == status
    assert copy_worker.read_text() == ""


def test_symlink_out_of_project_is_403(fake_plugins, project, copy_worker):
    fake_plugins("psconv", COPY_PLUGIN)
    ext.load(force=True)
    outside = project.parent / "secret.ps"
    outside.write_text("secret")
    (project / "plots" / "link.ps").symlink_to(outside)
    with pytest.raises(conv.ConvertError) as info:
        conv.convert(project, "plots/link.ps", "pdf")
    assert info.value.status == 403 and copy_worker.read_text() == ""


def test_source_size_cap_is_413(fake_plugins, project, copy_worker, monkeypatch):
    fake_plugins("psconv", COPY_PLUGIN)
    ext.load(force=True)
    monkeypatch.setattr(conv, "MAX_SOURCE", 4)
    with pytest.raises(conv.ConvertError) as info:
        conv.convert(project, "plots/a.ps", "pdf")
    assert info.value.status == 413 and copy_worker.read_text() == ""


def test_unknown_target_no_converter_and_missing_binary(fake_plugins, project, copy_worker):
    fake_plugins("psconv", COPY_PLUGIN)
    fake_plugins("needsgs", MISSING_PLUGIN)
    ext.load(force=True)
    (project / "notes.txt").write_text("hi")
    (project / "doc.xps").write_text("xps")
    cases = [("plots/a.ps", "svg", 400), ("notes.txt", "pdf", 404), ("plots/a.ps", "png", 404), ("doc.xps", "pdf", 503)]
    for rel, to, status in cases:
        with pytest.raises(conv.ConvertError) as info:
            conv.convert(project, rel, to)
        assert info.value.status == status, (rel, to, info.value)
    with pytest.raises(conv.ConvertError) as info:
        conv.convert(project, "notes.txt", "pdf")
    assert str(info.value) == "no converter for .txt → pdf"
    with pytest.raises(conv.ConvertError) as info:
        conv.convert(project, "doc.xps", "pdf")
    assert str(info.value) == "missing binary: alfrd-no-such-gs-xyz"
    assert copy_worker.read_text() == ""


def test_disabled_plugin_converts_nothing(fake_plugins, project, copy_worker):
    fake_plugins("psconv", COPY_PLUGIN)
    ext.load(force=True)
    ext.set_enabled("psconv", False)
    assert conv.find_converter(".ps", "pdf") is None
    with pytest.raises(conv.ConvertError) as info:
        conv.convert(project, "plots/a.ps", "pdf")
    assert info.value.status == 404


E2E_SOURCE = '''
import shutil
from alfrd.extensions import Plugin, Converter

def copy(src, dest, *, timeout):
    shutil.copyfile(src, dest)

def broken(src, dest, *, timeout):
    raise RuntimeError("converter exploded on " + src.name)

plugin = Plugin(id="e2econv", version="0.3", converters=[Converter(src=[".ps"], to="pdf", run=copy),
                                                         Converter(src=[".bad"], to="pdf", run=broken)])
'''


def test_end_to_end_real_worker(tmp_path, project):
    """No monkeypatching: a real installed plugin, found by the worker process via the plugin site."""
    wheel = build_wheel(tmp_path / "wheels", name="alfrd_e2econv", version="0.3", plugin_id="e2econv", source=E2E_SOURCE)
    site = ext.site_dir()
    site.mkdir(parents=True)
    with zipfile.ZipFile(wheel) as z:
        z.extractall(site)
    rec = next(r for r in ext.load(force=True) if r.id == "e2econv")
    assert rec.status == "ok", rec.error
    out = conv.convert(project, "plots/a.ps", "pdf")
    assert out.read_bytes() == (project / "plots" / "a.ps").read_bytes()
    (project / "x.bad").write_text("bad")
    with pytest.raises(conv.ConvertError) as info:
        conv.convert(project, "x.bad", "pdf")
    assert info.value.status == 502 and "converter exploded on x.bad" in info.value.detail_tail
    assert conv.main(["nope", "pdf", str(project / "plots" / "a.ps"), str(tmp_path / "o.pdf"), "5"]) == 2


def test_convert_route(studio_app, tmp_path, monkeypatch):
    app, _service = studio_app
    client = app.test_client()
    token = client.get("/api/studio/session").get_json()["csrf_token"]
    root = tmp_path / "consumer"
    root.mkdir()
    shutil.copy(PROJECT_ROOT / "examples" / "avica_0.3" / "alfrd.yaml", root / "alfrd.yaml")
    created = client.post("/api/projects/connect", json={"path": str(root)}, headers={"X-CSRF-Token": token})
    assert created.status_code == 201, created.get_json()
    name = created.get_json()["name"]

    pdf = tmp_path / "out.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    seen = {}

    def fake_convert(root_arg, rel, to, **kw):
        seen.update(root=root_arg, rel=rel, to=to)
        return pdf

    real_convert = conv.convert
    monkeypatch.setattr(conv, "convert", fake_convert)
    ok = client.get(f"/api/studio/projects/{name}/convert?path=plots/a.ps")
    assert ok.status_code == 200 and ok.mimetype == "application/pdf" and ok.data == pdf.read_bytes()
    assert ok.headers["X-Content-Type-Options"] == "nosniff" and ok.headers["Cache-Control"] == "no-cache"
    assert seen == {"root": root.resolve(), "rel": "plots/a.ps", "to": "pdf"}

    monkeypatch.setattr(conv, "convert", real_convert)  # no plugin converts .ps here
    (root / "a.ps").write_text("%!PS")
    missing = client.get(f"/api/studio/projects/{name}/convert?path=a.ps&to=pdf")
    assert missing.status_code == 404
    assert missing.get_json() == {"error": {"code": 404, "message": "no converter for .ps \u2192 pdf", "detail_tail": ""}}
    assert client.get(f"/api/studio/projects/{name}/convert?path=a.ps&to=svg").get_json()["error"]["code"] == 400
