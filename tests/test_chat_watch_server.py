"""Server scheduling, cron CLI and the Studio check contract."""
# ruff: noqa: F811 - imported pytest fixture is requested by name
from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from alfrd import chat, cli
from alfrd.manifest import ManifestError, parse_manifest
from tests.test_chat_orchestration import project  # noqa: F401 - fixture


def test_watch_starts_no_thread_without_projects(monkeypatch):
    service = SimpleNamespace(list_projects=lambda: [])
    monkeypatch.setattr(cli.threading, "Thread", lambda **kw: pytest.fail("empty watch started a thread"))
    assert cli._chat_watch_later(service, None, threading.Event(), spawn=True) is None


def test_manifest_accepts_watch_and_delegation_and_rejects_invalid_values():
    block = {"delegate_decisions": "all", "watch": dict(chat.DEFAULTS["watch"]), "bounds": {"max_replans": 0}}
    parse_manifest({"name": "chat", "chat": block})
    for value in (0, 30, 300):
        parse_manifest({"name": "chat", "chat": {"watch": {"interval_seconds": value}}})
    for watch in ({"interval_seconds": 29}, {"interval_seconds": True}, {"stall_seconds": 59},
                  {"max_checks": -1}, {"wake_orchestrator": "yes"}, {"enabled": "yes"}, {"typo": 1}):
        with pytest.raises(ManifestError, match="chat"):
            parse_manifest({"name": "chat", "chat": {"watch": watch}})
    with pytest.raises(ManifestError, match="delegate_decisions"):
        parse_manifest({"name": "chat", "chat": {"delegate_decisions": "sometimes"}})


def test_watch_respects_project_intervals_scope_and_survives_failure(monkeypatch, tmp_path, capsys):
    a, b, ignored = (tmp_path / n for n in ("a", "b", "ignored"))
    service = SimpleNamespace(list_projects=lambda: [SimpleNamespace(identifier=p.name, root_path=p) for p in (a, b, ignored)])
    monkeypatch.setattr(chat, "settings", lambda root: {"watch": {"interval_seconds": 30 if root == a else 60}})
    targets, checks = [], []
    clock = [0.0]

    class Thread:
        def __init__(self, **kwargs):
            assert kwargs["daemon"] is True
            targets.append(kwargs["target"])

        def start(self):
            pass

    class Stopped:
        def is_set(self):
            return clock[0] >= 100

        def wait(self, seconds):
            assert seconds > 0
            clock[0] += seconds

    def check(root, *, spawn, periodic):
        assert spawn is False and periodic is True
        checks.append((root, clock[0]))
        if len(checks) == 1:
            raise OSError("temporary failure")

    monkeypatch.setattr(cli.threading, "Thread", Thread)
    monkeypatch.setattr(chat, "check", check)
    cli._chat_watch_later(service, ["a", "b"], Stopped(), spawn=False)
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    targets[0]()
    assert checks == [(a, 0), (b, 0), (a, 30), (a, 60), (b, 60), (a, 90)]
    assert "temporary failure" in capsys.readouterr().out


def test_cli_check_json_and_unknown_session(project):
    sid = chat.create_session(project)["id"]
    result = CliRunner().invoke(cli.alfrd_cli, ["chat", "check", "--root", str(project), "--session", sid, "--json"])
    assert result.exit_code == 0, result.output
    [report] = json.loads(result.output)
    assert report["session"] == sid and report["findings"] == []
    assert json.loads((project / ".alfrd" / "chat" / sid / "check.json").read_text()) == report
    bad = CliRunner().invoke(cli.alfrd_cli, ["chat", "check", "--root", str(project), "--session", "missing", "--json"])
    assert bad.exit_code != 0


def test_check_updates_state_revision_without_idle_event(project):
    sid = chat.create_session(project)["id"]
    before = chat.session_state(project, sid)
    assert before["check"] is None
    [report] = chat.check(project, sid, spawn=False)
    state = chat.session_state(project, sid, since_rev=before["rev"])
    assert state["check"] == report and state["rev"] > before["rev"]
    assert report["at"].endswith("+00:00")


def test_studio_check_endpoint_csrf_and_missing_session(project, tmp_path, monkeypatch):
    from alfrd.gui import create_app
    from alfrd.gui.services import RuntimeCatalogReader
    from alfrd.runtime import RuntimeService, RuntimeStore

    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    service = RuntimeService(store)
    service.register_manifest(project / ".alfrd" / "alfrd.yaml")
    ident = service.list_projects()[0].identifier
    app = create_app({"TESTING": True, "SECRET_KEY": "k", "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'c.sqlite'}",
                      "RUNTIME_SERVICE": service, "CATALOG_READER": RuntimeCatalogReader(service),
                      "STUDIO_DEMO": False, "STUDIO_LIVE_INTERVAL": 0})
    client = app.test_client()
    headers = {"X-CSRF-Token": client.get("/api/studio/session").get_json()["csrf_token"]}
    sid = chat.create_session(project)["id"]
    url = f"/api/studio/projects/{ident}/chat/sessions/{sid}"
    monkeypatch.setattr("alfrd.runtime.scheduler.spawn_runner", lambda *args, **kwargs: pytest.fail("idle check spawned a runner"))
    assert client.post(url + "/check", json={}).status_code == 403
    response = client.post(url + "/check", json={}, headers=headers)
    assert response.status_code == 200
    report = response.get_json()
    assert report["session"] == sid and report["findings"] == []
    assert client.get(url).get_json()["check"] == report
    assert client.post(url.replace(sid, "missing") + "/check", json={}, headers=headers).status_code == 404


def test_checker_cli_toggle_preserves_interval(project):
    runner = CliRunner()
    for option, enabled in (("--disabled", False), ("--enabled", True)):
        result = runner.invoke(cli.alfrd_cli, ["su", "checker", "--root", str(project), option])
        assert result.exit_code == 0, result.output
        assert chat.settings(project)["watch"]["enabled"] is enabled
        assert chat.settings(project)["watch"]["interval_seconds"] == 300


def test_watch_can_enable_a_project_that_started_disabled(monkeypatch, tmp_path):
    service = SimpleNamespace(list_projects=lambda: [SimpleNamespace(identifier="p", root_path=tmp_path)])
    clock, checks, targets = [0], [], []
    monkeypatch.setattr(chat, "settings", lambda root: {"watch": {"enabled": clock[0] >= 30, "interval_seconds": 300}})
    monkeypatch.setattr(chat, "check", lambda *args, **kwargs: checks.append((clock[0], kwargs)))
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    class Thread:
        def __init__(self, **kwargs):
            targets.append(kwargs["target"])

        def start(self):
            pass

    class Stopped:
        def is_set(self):
            return clock[0] >= 60

        def wait(self, seconds):
            clock[0] += seconds

    monkeypatch.setattr(cli.threading, "Thread", Thread)
    cli._chat_watch_later(service, None, Stopped(), spawn=False)
    targets[0]()
    assert checks == [(30, {"spawn": False, "periodic": True})]
