"""Default inventory is usable offline, before optional plugins are installed."""
import importlib.metadata
from pathlib import Path

import pytest

from alfrd import extensions
from alfrd.extensions import defaults
from test_plugin_routes import plugin_pkgs  # noqa: F401
from test_studio import studio_app  # noqa: F401


def test_packaged_defaults_cover_bundled_examples():
    bundled = [p for p in defaults.discover() if p["source"] == "bundled"]
    examples = Path(__file__).resolve().parents[1] / "examples" / "plugins"
    assert {p["package"] for p in bundled} == {p.parent.name for p in examples.glob("*/pyproject.toml")}
    assert all(p["title"] and p["description"] and p["status"] == "available" for p in bundled)


@pytest.mark.parametrize("safe", [False, True])
def test_fresh_studio_lists_defaults_without_importing_plugins(studio_app, monkeypatch, safe):
    app, _ = studio_app
    monkeypatch.setattr(importlib.metadata, "entry_points", lambda **kw: [])
    if safe:
        monkeypatch.setenv("ALFRD_NO_PLUGINS", "1")
    extensions.load(force=True)
    data = app.test_client().get("/api/studio/plugins?project=example").get_json()
    assert data["plugins"] == []
    by_id = {p["id"]: p for p in data["defaults"]}
    for pid in ("telegram", "gsheet", "markdown", "ps2pdf"):
        entry = by_id[pid]
        assert entry["title"] and entry["description"]
        assert entry["status"] == "available" and not entry["installed"] and not entry["enabled"]
    assert by_id[extensions.DEFAULT_THEME]["status"] == "enabled"
    assert by_id["daylight-orbit"]["status"] == "installed"
    # Available metadata must not make plugin mutations possible.
    client = app.test_client()
    csrf = client.get("/api/studio/session").get_json()["csrf_token"]
    assert client.post("/api/studio/plugins/telegram/enable", headers={"X-CSRF-Token": csrf}).status_code == 404


@pytest.mark.parametrize("enabled", [False, True])
def test_installed_defaults_keep_actual_state_and_third_party_plugins(studio_app, plugin_pkgs, enabled):
    app, _ = studio_app
    plugin_pkgs("telegram", 'from alfrd.extensions import Plugin\nplugin = Plugin(id="telegram", title="My Telegram", version="9", description="Installed description")')
    plugin_pkgs("thirdparty", 'from alfrd.extensions import Plugin\nplugin = Plugin(id="thirdparty", version="1")')
    extensions.set_enabled("telegram", enabled)
    extensions.load(force=True)
    data = app.test_client().get("/api/studio/plugins").get_json()
    installed = {p["id"]: p for p in data["plugins"]}
    default = next(p for p in data["defaults"] if p["id"] == "telegram")
    assert default["installed"] and default["enabled"] == enabled and default["active"] == enabled
    assert default["status"] == ("ok" if enabled else "disabled")
    if enabled:
        assert default["description"] == "Installed description" and default["title"] == "My Telegram"
    else:
        assert default["description"] and default["title"] == "Telegram"
    assert installed["telegram"]["description"] == default["description"]
    assert installed["thirdparty"]["installed"] and installed["thirdparty"]["active"]
