"""Superuser settings (alfrd.superuser): the file, the Studio gate and page API, chat limits and `alfrd su`."""
from __future__ import annotations

import pytest
import yaml

from alfrd import superuser


def test_defaults_without_a_file_and_validation():
    assert not superuser.path().exists()
    assert superuser.load() == superuser.DEFAULTS
    with pytest.raises(superuser.SuperuserError, match="lockdown.runs must be true or false"):
        superuser.validate({"lockdown": {"runs": "no"}})
    with pytest.raises(superuser.SuperuserError, match="'overview' is not one of"):
        superuser.validate({"studio": {"hidden_sections": ["overview"]}})
    with pytest.raises(superuser.SuperuserError, match="unknown key"):
        superuser.validate({"limits": {"max_cost": 1}})
    with pytest.raises(superuser.SuperuserError, match="at least 0"):
        superuser.validate({"limits": {"chat_max_cost_usd": -1}})
    assert superuser.validate({"studio": {"hidden_sections": "results, logs"}})["studio"]["hidden_sections"] == ["results", "logs"]


def test_merge_save_reload_and_broken_file():
    superuser.merge({"features.experimental": True, "lockdown": {"runs": False}})
    text = superuser.path().read_text()
    assert text.startswith("# ALFRD superuser settings") and yaml.safe_load(text)["lockdown"]["runs"] is False
    assert superuser.load()["features"]["experimental"] is True
    assert not superuser.allowed("runs") and superuser.allowed("chat")
    superuser.merge({"lockdown.read_only": True})
    assert not superuser.allowed("chat")
    superuser.path().write_text("features: [oops\n")
    # a broken file never unlocks: the last good settings stay in force
    assert superuser.load()["lockdown"]["read_only"] is True and "superuser.yaml" in superuser.error()
    with pytest.raises(superuser.SuperuserError):
        superuser.load(strict=True)
    with pytest.raises(superuser.SuperuserError):  # never rebuilt from guesses
        superuser.merge({"features.experimental": False})
    assert superuser.path().read_text() == "features: [oops\n"
    superuser.reset()
    assert not superuser.path().exists() and superuser.error() is None


def test_broken_file_without_good_settings_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setenv("ALFRD_SUPERUSER_FILE", str(tmp_path / "other.yaml"))
    (tmp_path / "other.yaml").write_text("lockdown: {read_only: false, runs: maybe}\n")
    assert superuser.load()["lockdown"]["read_only"] is True and superuser.error()


def test_numbers_must_be_finite():
    for bad in ("inf", "nan", "-inf"):
        with pytest.raises(superuser.SuperuserError, match="finite"):
            superuser.validate({"studio": {"live_interval": bad}})


def test_parse_value_for_the_cli():
    assert superuser.parse_value("lockdown.runs", "off") is False
    assert superuser.parse_value("studio.hidden_sections", "results,logs") == ["results", "logs"]
    assert superuser.parse_value("limits.chat_max_cost_usd", "null") is None
    with pytest.raises(superuser.SuperuserError, match="unknown setting"):
        superuser.parse_value("lockdown.nope", "1")


def test_chat_limits_cap_bounds_and_mark_allowed_agents():
    from alfrd import chat

    conf = {"orchestrator": "claude", "bounds": {"max_cost_usd": 5.0, "max_replans": 3},
            "agents": {"claude": {}, "sonnet": {"fallback": "codex"}, "codex": {}}}
    superuser.merge({"limits": {"chat_max_cost_usd": 1.5, "chat_max_replans": 1, "chat_allowed_agents": ["claude", "sonnet"]}})
    out = superuser.cap_chat_settings(conf)
    assert out["bounds"] == {"max_cost_usd": 1.5, "max_replans": 1}
    # configured agents stay (running rows keep their columns); new work only goes to allowed ones
    assert set(out["agents"]) == {"claude", "sonnet", "codex"} and set(chat.usable(out)) == {"claude", "sonnet"}
    superuser.reset()
    assert set(chat.usable(superuser.cap_chat_settings(dict(conf, agents=dict(conf["agents"]))))) == {"claude", "sonnet", "codex"}


def test_disallowed_agents_get_no_new_chat_work(tmp_path):
    from alfrd import chat
    from tests.test_chat_orchestration import FAKE  # noqa: F401  (same fixture style; only settings are needed)

    root = tmp_path / "proj"
    (root / ".alfrd").mkdir(parents=True)
    (root / ".alfrd" / "alfrd.yaml").write_text(yaml.safe_dump({"version": 1, "name": "p", "chat": {}}))
    sid = chat.create_session(root, "x")["id"]
    superuser.merge({"limits.chat_allowed_agents": ["codex"]})
    with pytest.raises(PermissionError, match="orchestrator 'claude' is not an allowed agent"):
        chat.post_message(root, sid, "plan something", spawn=False)
    with pytest.raises(PermissionError, match="@sonnet is not an allowed agent"):
        chat.post_message(root, sid, "@sonnet hello", spawn=False)
    assert chat.post_message(root, sid, "@codex hello", spawn=False)["task"]["owner"] == "codex"
    assert chat.settings(root)["agents"].keys() >= {"claude", "sonnet", "codex"}  # settings still load (cancel keeps working)


@pytest.fixture
def client(tmp_path):
    pytest.importorskip("flask")
    from alfrd.gui import create_app
    from alfrd.gui.services import RuntimeCatalogReader
    from alfrd.runtime import RuntimeService, RuntimeStore

    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    service = RuntimeService(store)
    app = create_app({"TESTING": True, "SECRET_KEY": "k", "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'c.sqlite'}",
                      "RUNTIME_SERVICE": service, "CATALOG_READER": RuntimeCatalogReader(service),
                      "STUDIO_DEMO": False, "STUDIO_LIVE_INTERVAL": 2})
    c = app.test_client()
    c.h = {"X-CSRF-Token": c.get("/api/studio/session").get_json()["csrf_token"]}
    return c


def test_page_api_is_local_and_csrf_guarded(client):
    assert client.get("/api/studio/superuser").status_code == 403  # no CSRF header
    view = client.get("/api/studio/superuser", headers=client.h).get_json()
    assert view["settings"] == superuser.DEFAULTS and view["effective"]["runs"] is True and view["exists"] is False
    assert client.post("/api/studio/superuser", json={"settings": {"lockdown.runs": False}}).status_code == 403
    bad = client.post("/api/studio/superuser", json={"settings": {"lockdown.bogus": 1}}, headers=client.h)
    assert bad.status_code == 400 and "lockdown.bogus" in bad.get_json()["error"]["message"]
    page = client.get("/superuser?x=1")
    assert page.status_code == 302 and page.headers["Location"].endswith("/studio/?x=1#/superuser")


def test_settings_change_what_the_studio_shows_and_allows(client, tmp_path):
    session = lambda: client.get("/api/studio/session").get_json()  # noqa: E731
    templates = lambda: [t["name"] for t in client.get("/api/studio/project-templates").get_json()["templates"]]  # noqa: E731
    assert session()["experimental"] is False and templates() == ["avica", "basic"]
    saved = client.post("/api/studio/superuser", headers=client.h, json={"settings": {
        "features": {"experimental": True}, "studio": {"hidden_sections": ["results"], "hidden_templates": ["avica"], "live_interval": 0},
        "lockdown": {"project_create": False}}})
    assert saved.status_code == 200 and saved.get_json()["effective"]["live_interval"] == 0
    s = session()
    assert s["experimental"] is True and s["superuser"]["hidden_sections"] == ["results"] and s["live"]["enabled"] is False
    assert s["superuser"]["allowed"]["project_create"] is False
    assert templates() == ["agent-loop", "basic"]
    refused = client.post("/api/studio/projects/create", json={"path": str(tmp_path / "p")}, headers=client.h)
    assert refused.status_code == 403 and refused.get_json()["error"]["reason"] == "superuser"
    # read-only: every change is refused, except stopping work and the superuser page itself
    client.post("/api/studio/superuser", headers=client.h, json={"settings": {"lockdown.read_only": True}})
    notes = client.post("/api/studio/projects/x/notes", json={}, headers=client.h)
    assert notes.status_code == 403 and "read-only" in notes.get_json()["error"]["message"]
    assert client.post("/api/studio/projects/x/plans/p1/pause", json={}, headers=client.h).status_code != 403
    assert client.post("/api/studio/projects/x/plans/p1/resume", json={}, headers=client.h).status_code == 403
    resume = client.post("/api/studio/superuser/ops", json={"project": "x", "plan": "p1", "action": "resume"}, headers=client.h)
    assert resume.status_code == 403 and resume.get_json()["error"]["reason"] == "superuser"
    assert client.post("/api/studio/superuser/reset", headers=client.h, json={}).status_code == 200
    assert client.post("/api/studio/projects/x/notes", json={}, headers=client.h).status_code != 403


def test_plugin_switches_follow_the_lockdown(client):
    client.post("/api/studio/superuser", headers=client.h, json={"settings": {"lockdown.plugins": False, "lockdown.plugin_actions": False}})
    data = client.get("/api/studio/plugins").get_json()
    assert data["gui_install"] is False and data["plugin_actions"] is False
    install = client.post("/api/studio/plugins/install", json={"spec": "x"}, headers=client.h)
    assert install.status_code == 403


def test_ops_lists_active_runs_and_stops_them(client, tmp_path):
    from alfrd.runtime import scheduler

    ops = client.get("/api/studio/superuser/ops", headers=client.h).get_json()
    assert ops["stats"]["projects"] == 0 and ops["runs"] == [] and ops["system"]["version"]
    bad = client.post("/api/studio/superuser/ops", json={"project": "x", "plan": "p", "action": "explode"}, headers=client.h)
    assert bad.status_code == 400
    assert scheduler.ACTIVE  # the panel lists plans in these states or with a live runner


def test_cli_su(tmp_path):
    from typer.testing import CliRunner

    from alfrd.cli import alfrd_cli

    runner = CliRunner()
    out = runner.invoke(alfrd_cli, ["su", "set", "lockdown.runs=false", "studio.hidden_sections=logs"])
    assert out.exit_code == 0, out.output
    assert superuser.load()["lockdown"]["runs"] is False
    shown = runner.invoke(alfrd_cli, ["su", "show"]).output
    assert "* lockdown.runs" in shown and "* studio.hidden_sections" in shown
    assert runner.invoke(alfrd_cli, ["su", "set", "lockdown.runs=maybe"]).exit_code == 1
    assert runner.invoke(alfrd_cli, ["su", "path"]).output.strip() == str(superuser.path())
    assert runner.invoke(alfrd_cli, ["su", "reset", "--yes"]).exit_code == 0 and not superuser.path().exists()


def test_a_broken_file_is_replaced_only_on_purpose(client):
    superuser.path().parent.mkdir(parents=True, exist_ok=True)
    superuser.path().write_text("lockdown: [broken\n")
    view = client.get("/api/studio/superuser", headers=client.h).get_json()
    assert view["error"] and view["settings"] == superuser.DEFAULTS  # the last good settings (no file: defaults)
    assert client.post("/api/studio/superuser", json={"settings": {"lockdown.runs": False}}, headers=client.h).status_code == 400
    fixed = client.post("/api/studio/superuser", json={"settings": superuser.DEFAULTS, "replace": True}, headers=client.h)
    assert fixed.status_code == 200 and fixed.get_json()["error"] is None and fixed.get_json()["effective"]["read_only"] is False
