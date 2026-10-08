"""Plugins Phase 3 routes: list, enable/disable, theme, and plugin browser files."""
from __future__ import annotations

import importlib.metadata
import sys
import textwrap

import pytest

from alfrd import extensions
from alfrd.extensions import scaffold
from test_studio import studio_app  # noqa: F401  (fixture)


@pytest.fixture
def plugin_pkgs(tmp_path, monkeypatch):
    """``add(id, manifest, files)`` writes package ``alfrd_rt_<id>`` with an entry point; ``load()`` applies them."""
    base = tmp_path / "pkgs"
    base.mkdir()
    monkeypatch.syspath_prepend(str(base))
    eps: list = []
    monkeypatch.setattr(importlib.metadata, "entry_points",
                        lambda group=None, **kw: [e for e in eps if group in (None, e.group)])

    def add(plugin_id: str, manifest: str, files: dict[str, str] | None = None):
        module = f"alfrd_rt_{plugin_id}"
        pkg = base / module
        pkg.mkdir()
        (pkg / "__init__.py").write_text(textwrap.dedent(manifest))
        for rel, text in (files or {}).items():
            (pkg / rel).parent.mkdir(parents=True, exist_ok=True)
            (pkg / rel).write_text(text)
        sys.modules.pop(module, None)
        eps.append(importlib.metadata.EntryPoint(plugin_id, f"{module}:plugin", extensions.GROUP))
        return pkg

    yield add
    extensions.reset()
    for name in list(sys.modules):
        if name.startswith("alfrd_rt_"):
            sys.modules.pop(name)


WEB = """
from alfrd.extensions import Plugin
plugin = Plugin(id="webby", version="1.0", title="Webby", web="web", viewers=["webby"], theme="look.css")
"""
PANEL = """
from alfrd.extensions import Plugin, PanelSpec
plugin = Plugin(id="pany", version="2", panels=[PanelSpec("pany_panel", client=True)], web="web")
"""
BROKEN = "raise RuntimeError('boom')\n"


def _setup(plugin_pkgs, tmp_path):
    pkg = plugin_pkgs("webby", WEB, {"web/index.js": "export function activate(api) {}\n", "web/index.css": "a{}",
                                     "look.css": ":root { --bg: #123; }", "secret.txt": "no"})
    plugin_pkgs("pany", PANEL, {"web/index.js": "export function activate() {}\n"})
    plugin_pkgs("broken", BROKEN)
    extensions.load(force=True)
    return pkg


def _csrf(client):
    return {"X-CSRF-Token": client.get("/api/studio/session").get_json()["csrf_token"]}


def test_list_reports_state_web_files_and_errors(studio_app, plugin_pkgs, tmp_path):
    app, _ = studio_app
    _setup(plugin_pkgs, tmp_path)
    data = app.test_client().get("/api/studio/plugins").get_json()
    by_id = {p["id"]: p for p in data["plugins"]}
    webby = by_id["webby"]
    assert webby["active"] and webby["enabled"] and webby["status"] == "ok" and webby["title"] == "Webby"
    assert webby["web"] == {"js": "/studio/plugins/webby/index.js", "css": "/studio/plugins/webby/index.css"}
    assert webby["theme_css"] == "/studio/plugins/webby/theme.css" and not webby["has_python"]
    assert by_id["pany"]["web"]["css"] is None and by_id["pany"]["has_python"]
    broken = by_id["broken"]
    assert broken["status"] == "error" and "boom" in broken["error"] and broken["traceback_tail"]
    assert broken["web"] is None and not broken["active"]
    assert data["theme"] == extensions.DEFAULT_THEME
    ids = {t["id"]: t["source"] for t in data["themes"]}
    assert ids["obsidian-orbit"] == "builtin" and ids["daylight-orbit"] == "builtin" and ids["webby"] == "plugin"


def test_plugin_files_are_confined_typed_and_follow_disable(studio_app, plugin_pkgs, tmp_path):
    app, _ = studio_app
    pkg = _setup(plugin_pkgs, tmp_path)
    client = app.test_client()
    js = client.get("/studio/plugins/webby/index.js")
    assert js.status_code == 200 and js.mimetype == "text/javascript"
    assert js.headers["X-Content-Type-Options"] == "nosniff" and js.headers["Cache-Control"] == "no-cache"
    theme = client.get("/studio/plugins/webby/theme.css")
    assert theme.status_code == 200 and theme.mimetype == "text/css" and b"#123" in theme.data
    (tmp_path / "outside.js").write_text("secret")
    (pkg / "web" / "link.js").symlink_to(tmp_path / "outside.js")
    for bad in ("../secret.txt", "../__init__.py", "link.js", "missing.js", "%2e%2e/secret.txt"):
        assert client.get(f"/studio/plugins/webby/{bad}").status_code == 404, bad
    assert client.get("/studio/plugins/broken/index.js").status_code == 404
    assert client.get("/studio/plugins/nobody/index.js").status_code == 404
    # Public theme.css never serves a plugin theme (D3): it falls back to the default.
    extensions.set_theme("webby")
    assert b"#123" not in client.get("/studio/theme.css").data
    extensions.set_enabled("webby", False)
    assert client.get("/studio/plugins/webby/index.js").status_code == 404
    entry = next(p for p in client.get("/api/studio/plugins").get_json()["plugins"] if p["id"] == "webby")
    assert not entry["enabled"] and not entry["active"] and entry["web"] is None


def test_toggle_and_theme_need_csrf_and_report_restart(studio_app, plugin_pkgs, tmp_path):
    app, _ = studio_app
    _setup(plugin_pkgs, tmp_path)
    client = app.test_client()
    assert client.post("/api/studio/plugins/webby/disable").status_code == 403
    assert client.post("/api/studio/theme", json={"id": "daylight-orbit"}).status_code == 403
    headers = _csrf(client)
    off = client.post("/api/studio/plugins/webby/disable", headers=headers)
    assert off.status_code == 200 and off.get_json()["restart_required"] is False
    assert "webby" in extensions.read_state()["disabled"]
    on = client.post("/api/studio/plugins/webby/enable", headers=headers).get_json()
    assert on["restart_required"] is False and on["plugin"]["active"]  # still loaded: browser-only change
    assert client.post("/api/studio/plugins/pany/disable", headers=headers).get_json()["restart_required"] is True
    assert client.post("/api/studio/plugins/broken/enable", headers=headers).get_json()["restart_required"] is True
    assert client.post("/api/studio/plugins/nobody/enable", headers=headers).status_code == 404
    assert client.post("/api/studio/plugins/webby/explode", headers=headers).status_code == 404
    bad = client.post("/api/studio/theme", json={"id": "nope"}, headers=headers)
    assert bad.status_code == 400 and "obsidian-orbit" in bad.get_json()["error"]["themes"]
    assert client.post("/api/studio/theme", json={"id": "webby"}, headers=headers).get_json() == {"theme": "webby"}
    assert extensions.read_state()["theme"] == "webby"
    remote = client.post("/api/studio/theme", json={"id": "daylight-orbit"}, headers=headers,
                         environ_base={"REMOTE_ADDR": "10.0.0.5"})
    assert remote.status_code == 403 and extensions.read_state()["theme"] == "webby"


def test_safe_mode_lists_without_web(studio_app, plugin_pkgs, tmp_path, monkeypatch):
    app, _ = studio_app
    _setup(plugin_pkgs, tmp_path)
    monkeypatch.setenv("ALFRD_NO_PLUGINS", "1")
    extensions.load(force=True)
    data = app.test_client().get("/api/studio/plugins").get_json()
    assert data["safe_mode"] is True
    assert all(p["status"] == "skipped (safe mode)" and p["web"] is None for p in data["plugins"])
    assert app.test_client().get("/studio/plugins/webby/index.js").status_code == 404


def test_scaffolded_converter_takes_keyword_timeout(tmp_path):
    import inspect

    files = scaffold._files("conv", "converter")
    namespace: dict = {}
    source = next(text for path, text in files.items() if path.endswith("__init__.py"))
    exec(compile(source, "scaffold", "exec"), namespace)  # noqa: S102 - our own template
    params = inspect.signature(namespace["convert"]).parameters
    assert params["timeout"].kind is inspect.Parameter.KEYWORD_ONLY
