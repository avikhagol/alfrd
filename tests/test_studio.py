"""Packaging, CLI and server integration tests for ALFRD Studio."""

from __future__ import annotations

import gzip
import os
import re
import shutil
import subprocess
import threading
import urllib.request
from importlib import resources
from pathlib import Path

import pytest
from typer.testing import CliRunner

from alfrd import __version__
from alfrd.cli import _studio_handler, alfrd_cli
from alfrd.web import REQUIRED_ASSETS, export_site, missing_assets, web_root

runner = CliRunner()
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _web_files():
    root = web_root()
    return [p for p in root.rglob("*") if p.is_file() and p.suffix not in {".py", ".pyc"} and "__pycache__" not in p.parts]


def test_studio_assets_are_packaged():
    root = resources.files("alfrd.web")
    for name in REQUIRED_ASSETS:
        assert root.joinpath(*name.split("/")).is_file(), name
    assert missing_assets() == []


def test_index_references_local_assets_only():
    html = (web_root() / "index.html").read_text(encoding="utf-8")
    assert 'src="js/app.js"' in html and 'type="module"' in html
    assert 'href="css/studio.css"' in html
    for path in _web_files():
        if path.suffix in {".html", ".js", ".css"}:
            text = path.read_text(encoding="utf-8")
            # No third-party runtime scripts, stylesheets or fonts.
            assert not re.search(r"""(src|href)\s*=\s*["']https?://""", text), path
            assert not re.search(r"""import\s[^;]*from\s+["']https?://""", text), path
            assert "@import url(" not in text, path


_STATIC_IMPORT = re.compile(
    r"""^\s*(?:import|export)\s[^;]*?\bfrom\s+["'](\.[^"']+)["']|^\s*import\s+["'](\.[^"']+)["']""",
    re.M,
)
#: Modules the Studio loads with ``import()`` on first use; never part of startup.
LAZY_MODULES = {
    "js/components/agent_dialog.js",
    "js/components/diagnostics.js", "js/components/targets_dialog.js", "js/data/targets.js",
    "js/components/step_picker.js", "js/data/step_select.js", "js/components/history.js",
    "js/components/palette.js", "js/data/fuzzy.js", "js/components/usage_view.js",
    "js/components/search_view.js", "js/components/notes_panel.js", "js/data/entities.js",
    "js/components/panels.js", "js/components/metadata_avica.js",
}


def _startup_files() -> set[Path]:
    """index.html, studio.css and the static import closure of js/app.js."""
    root = web_root()
    seen: set[Path] = set()
    todo = [root / "js" / "app.js"]
    while todo:
        path = todo.pop().resolve()
        if path in seen:
            continue
        seen.add(path)
        for match in _STATIC_IMPORT.finditer(path.read_text(encoding="utf-8")):
            todo.append(path.parent / (match.group(1) or match.group(2)))
    return seen | {(root / "index.html").resolve(), (root / "css" / "studio.css").resolve()}


def _gz(path: Path) -> int:
    return len(gzip.compress(path.read_bytes(), 9))


def test_startup_payload_under_165kb_compressed():
    # What the Studio loads when it opens. 150 KB until 0.2.1 (~149 KB then);
    # scheduled runs added ~12 KB. Features opened on demand load with import()
    # and count under the lazy cap below instead.
    total = sum(_gz(p) for p in _startup_files())
    assert total < 165 * 1024, f"{total} bytes gzip"


def test_lazy_payload_under_57kb_compressed():
    # Everything else: import() modules, css/lazy.css, templates and assets.
    # 40 KB until 0.2.0.7; 0.2.0.8's on-demand features (palette, search,
    # history, notes, usage, step picker, panels) load here, not at startup.
    startup = _startup_files()
    total = sum(_gz(p) for p in _web_files() if p.resolve() not in startup)
    # Agent-loop artifact paging, copy and refresh controls add under 1 KB gzip.
    assert total < 57 * 1024, f"{total} bytes gzip"


def test_lazy_modules_are_not_imported_statically():
    root = web_root()
    startup = {os.path.relpath(p, root.resolve()).replace(os.sep, "/") for p in _startup_files()}
    assert not (LAZY_MODULES & startup), sorted(LAZY_MODULES & startup)


def test_no_pipeline_names_are_hardcoded_in_the_studio_code():
    """VASCO names only live in alfrd.yaml field aliases (demo data included)."""
    allowed = {"demo.js"}
    for path in _web_files():
        if path.suffix != ".js":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line in text.splitlines():
            if re.search(r"vasco", line, re.I):
                assert path.name in allowed, f"{path.name}: {line.strip()}"


def test_cli_studio_help_and_export(tmp_path):
    result = runner.invoke(alfrd_cli, ["studio", "--help"])
    assert result.exit_code == 0, result.output
    # CI forces colour; rich may split option names with ANSI codes.
    plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    assert "--port" in plain and "--no-browser" in plain

    out = tmp_path / "site"
    result = runner.invoke(alfrd_cli, ["studio", "--export", str(out)])
    assert result.exit_code == 0, result.output
    assert (out / "index.html").is_file()
    assert (out / "js" / "components" / "canvas.js").is_file()
    assert (out / ".nojekyll").is_file()
    assert not list(out.rglob("*.py"))
    assert export_site(out) == out.resolve()


def test_static_handler_serves_modules_with_js_mime():
    from http.server import ThreadingHTTPServer

    server = ThreadingHTTPServer(("127.0.0.1", 0), _studio_handler(web_root()))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with opener.open(f"{base}/") as response:
            assert b"ALFRD Studio" in response.read()
        with opener.open(f"{base}/js/app.js") as response:
            assert response.headers["Content-Type"].startswith("text/javascript")
            assert response.headers["X-Content-Type-Options"] == "nosniff"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def studio_app(tmp_path: Path):
    pytest.importorskip("flask")
    from alfrd.gui import create_app
    from alfrd.gui.services import RuntimeCatalogReader
    from alfrd.runtime import RuntimeService, RuntimeStore

    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    service = RuntimeService(store)
    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'catalog.sqlite'}",
            "RUNTIME_SERVICE": service,
            "CATALOG_READER": RuntimeCatalogReader(service),
        }
    )
    return app, service


def test_flask_serves_studio_and_blocks_python_files(studio_app):
    app, _ = studio_app
    client = app.test_client()
    assert client.get("/").headers["Location"].endswith("/studio/")
    assert client.get("/studio").status_code in {301, 308}
    page = client.get("/studio/")
    assert page.status_code == 200 and b"ALFRD Studio" in page.data
    js = client.get("/studio/js/app.js")
    assert js.status_code == 200 and js.mimetype == "text/javascript"
    assert client.get("/studio/__init__.py").status_code == 404
    assert client.get("/studio/../cli.py").status_code == 404
    assert client.get("/dashboard/").status_code == 200


def test_studio_session_and_json_connect(studio_app, tmp_path):
    app, service = studio_app
    client = app.test_client()
    session = client.get("/api/studio/session").get_json()
    assert session["app"] == "alfrd"
    assert session["version"] == __version__
    assert session["runtime_enabled"] is True
    assert session["mutations_enabled"] is True
    token = session["csrf_token"]

    project = tmp_path / "consumer"
    project.mkdir()
    shutil.copy(PROJECT_ROOT / "examples" / "avica_0.3" / "alfrd.yaml", project / "alfrd.yaml")

    denied = client.post("/api/projects/connect", json={"path": str(project)})
    assert denied.status_code == 403

    created = client.post("/api/projects/connect", json={"path": str(project)}, headers={"X-CSRF-Token": token})
    assert created.status_code == 201, created.get_json()
    assert created.get_json()["name"] == "avica-0.3"
    assert service.get_project_by_name("avica-0.3").root_path == str(project.resolve())

    bad = client.post("/api/projects/connect", json={"path": str(tmp_path / "missing")}, headers={"X-CSRF-Token": token})
    assert bad.status_code == 400
    assert "error" in bad.get_json()

    assert client.get("/api/projects/avica-0.3/workflows").status_code == 200
    assert any(p["name"] == "avica-0.3" for p in client.get("/api/projects").get_json()["projects"])


def test_js_parsers_with_node():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js not available")
    result = subprocess.run(
        [node, "--test", *sorted(str(p) for p in (PROJECT_ROOT / "tests" / "studio_js").glob("*.test.mjs"))],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
