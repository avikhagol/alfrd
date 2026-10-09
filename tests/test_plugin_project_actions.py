"""Plugin project actions (``POST /studio/projects/<p>/plugins/<id>/actions/<action>``) and automatic panels."""
from __future__ import annotations

import json
import sys

import pytest
from test_plugin_routes import _csrf, plugin_pkgs  # noqa: F401  (fixture)
from test_studio import studio_app  # noqa: F401  (fixture)

from alfrd import extensions
from alfrd import layout_generic as lg
from alfrd.extensions import PanelSpec, Plugin, ProjectAction

ACTIONS = '''
import time
from alfrd.extensions import PanelSpec, Plugin, ProjectAction
CALLS = []

def echo(root, values, payload):
    CALLS.append("echo")
    return {"payload": payload, "has_values": isinstance(values, dict)}

def write(root, values, payload):
    CALLS.append("write")
    (root / "out.txt").write_text("written")
    return {"written": True}

def bad(root, values, payload):
    CALLS.append("bad")
    raise ValueError("The sheet was not\\nfound.")

def boom(root, values, payload):
    CALLS.append("boom")
    raise RuntimeError("SECRET-CREDENTIAL in a library error")

def slow(root, values, payload):
    CALLS.append("slow")
    time.sleep(1.5)
    return {}

def big(root, values, payload):
    return {"x": "a" * (3 * 1024 * 1024)}

def listed(root, values, payload):
    return [1, 2]

def unsafe(root, values, payload):
    return {"x": object()}

plugin = Plugin(id="acts", version="1", project_actions=[
    ProjectAction("echo", echo), ProjectAction("write", write, mutating=True), ProjectAction("bad", bad, mutating=True),
    ProjectAction("boom", boom), ProjectAction("slow", slow, mutating=True, timeout=0.2), ProjectAction("big", big),
    ProjectAction("listed", listed), ProjectAction("unsafe", unsafe),
], panels=[PanelSpec("acts_status", client=True, auto=lambda root: {"title": "Acts", "scope": "project"}
                     if (root / "acts.yaml").exists() else None)])
'''

URL = "/api/studio/projects/example/plugins/acts/actions/"


@pytest.fixture
def acts(studio_app, plugin_pkgs, tmp_path, monkeypatch):  # noqa: F811
    from alfrd.gui import studio as studio_module

    app, _ = studio_app
    plugin_pkgs("acts", ACTIONS)
    plugin_pkgs("plain", "from alfrd.extensions import Plugin\nplugin = Plugin(id='plain', version='1')\n")
    extensions.load(force=True)
    roots: list[str] = []
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(studio_module, "_project_root", lambda name: roots.append(name) or project)
    client = app.test_client()
    yield app, client, _csrf(client), sys.modules["alfrd_rt_acts"].CALLS, roots, project


def _audit() -> list[dict]:
    path = extensions.plugins_dir() / "audit.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_gates_run_before_project_or_plugin_code(acts):
    _, client, csrf, calls, roots, _ = acts
    assert client.post(URL + "write", json={}).status_code == 403
    assert client.post(URL + "write", json={}, headers=csrf, environ_base={"REMOTE_ADDR": "10.0.0.5"}).status_code == 403
    assert client.post(URL + "nope", json={}, headers=csrf).status_code == 404
    assert client.post("/api/studio/projects/example/plugins/plain/actions/echo", headers=csrf).status_code == 404
    assert client.post("/api/studio/projects/example/plugins/ghost/actions/echo", headers=csrf).status_code == 404
    assert calls == [] and roots == [] and _audit() == []


def test_payload_result_and_size_caps(acts):
    _, client, csrf, calls, roots, _ = acts
    reply = client.post(URL + "echo", json={"spreadsheet": "abc", "n": [1, 2]}, headers=csrf)
    assert reply.status_code == 200
    assert reply.get_json() == {"ok": True, "data": {"payload": {"spreadsheet": "abc", "n": [1, 2]}, "has_values": True}}
    assert client.post(URL + "echo", headers=csrf).get_json()["data"]["payload"] == {}
    assert client.post(URL + "echo", data="[1]", headers=csrf, content_type="application/json").status_code == 400
    assert client.post(URL + "echo", data="{bad", headers=csrf, content_type="application/json").status_code == 400
    huge = json.dumps({"x": "a" * (256 * 1024)})
    assert client.post(URL + "echo", data=huge, headers=csrf, content_type="application/json").status_code == 413
    assert calls == ["echo", "echo"] and roots == ["example", "example"]
    assert client.post(URL + "big", headers=csrf).get_json() == {"ok": False, "error": "The action result is too large."}
    assert client.post(URL + "listed", headers=csrf).get_json() == {"ok": False, "error": "The action failed (TypeError)."}
    assert client.post(URL + "unsafe", headers=csrf).get_json() == {"ok": False, "error": "The action failed (TypeError)."}
    assert _audit() == []  # read-only actions are not audited


def test_errors_value_error_text_vs_hidden_exception(acts):
    _, client, csrf, _, _, _ = acts
    bad = client.post(URL + "bad", json={}, headers=csrf)
    assert bad.status_code == 200 and bad.get_json() == {"ok": False, "error": "The sheet was not found."}
    boom = client.post(URL + "boom", json={}, headers=csrf)
    assert boom.status_code == 200 and boom.get_json() == {"ok": False, "error": "The action failed (RuntimeError)."}
    assert "SECRET" not in boom.get_data(as_text=True)


def test_timeout_is_504_and_audited(acts):
    _, client, csrf, calls, _, _ = acts
    reply = client.post(URL + "slow", json={}, headers=csrf)
    assert reply.status_code == 504 and reply.get_json()["error"]["message"] == "The action took longer than 0.2 s."
    assert calls == ["slow"]
    assert [(a["action"], a["action_id"], a["result"]) for a in _audit()] == [("project_action", "slow", "timeout")]


def test_mutating_calls_are_audited_without_payload(acts):
    _, client, csrf, _, _, project = acts
    reply = client.post(URL + "write", json={"token": "PAYLOAD-SECRET-XYZ"}, headers=csrf)
    assert reply.get_json() == {"ok": True, "data": {"written": True}}
    assert (project / "out.txt").read_text() == "written"
    client.post(URL + "bad", json={"token": "PAYLOAD-SECRET-XYZ"}, headers=csrf)
    records = _audit()
    assert [(r["action"], r["id"], r["action_id"], r["project"], r["result"]) for r in records] == [
        ("project_action", "acts", "write", "example", "ok"), ("project_action", "acts", "bad", "example", "failed")]
    assert records[1]["error"] == "The sheet was not found."
    assert "PAYLOAD-SECRET-XYZ" not in (extensions.plugins_dir() / "audit.jsonl").read_text()


def test_plugin_actions_policy_refuses_only_mutating_actions(acts):
    app, client, csrf, calls, roots, _ = acts
    extensions.state_file().parent.mkdir(parents=True, exist_ok=True)
    extensions.state_file().write_text('{"plugin_actions": false}')
    refused = client.post(URL + "write", json={}, headers=csrf)
    assert refused.status_code == 403 and refused.get_json()["error"]["reason"] == "plugin_actions"
    assert client.get("/api/studio/plugins").get_json()["plugin_actions"] is False
    assert client.post(URL + "echo", json={}, headers=csrf).get_json()["ok"] is True
    assert calls == ["echo"] and roots == ["example"]
    assert [(a["action_id"], a["result"]) for a in _audit()] == [("write", "refused")]
    extensions.state_file().write_text("{}")
    app.config["PLUGINS_ACTIONS"] = False
    try:
        assert client.post(URL + "write", json={}, headers=csrf).status_code == 403
        assert client.get("/api/studio/plugins").get_json()["plugin_actions"] is False
    finally:
        app.config["PLUGINS_ACTIONS"] = True
    assert calls == ["echo"]


def test_disabled_plugin_and_safe_mode_run_nothing(acts, monkeypatch):
    _, client, csrf, calls, roots, project = acts
    (project / "acts.yaml").write_text("x: 1\n")
    assert lg.AUTO_PANELS["acts_status"](project) == {"title": "Acts", "scope": "project"}
    assert lg.AUTO_PANELS["acts_status"](project.parent) is None
    extensions.set_enabled("acts", False)
    assert client.post(URL + "echo", json={}, headers=csrf).status_code == 404
    assert lg.AUTO_PANELS["acts_status"](project) is None  # disabled: no panel before the restart either
    extensions.set_enabled("acts", True)
    monkeypatch.setenv("ALFRD_NO_PLUGINS", "1")
    assert lg.AUTO_PANELS["acts_status"](project) is None
    extensions.load(force=True)
    assert client.post(URL + "echo", json={}, headers=csrf).status_code == 404
    assert "acts_status" not in lg.AUTO_PANELS
    assert calls == [] and roots == []


def test_manifest_validation():
    def run(root, values, payload):
        return {}

    with pytest.raises(ValueError, match="invalid project action id"):
        ProjectAction("Bad Id", run)
    with pytest.raises(TypeError, match="run must be callable"):
        ProjectAction("ok", "not callable")
    for timeout in (0, 601, True, "5"):
        with pytest.raises(ValueError, match="timeout"):
            ProjectAction("ok", run, timeout=timeout)
    with pytest.raises(ValueError, match="unique"):
        Plugin(id="p", version="1", project_actions=[ProjectAction("a", run), ProjectAction("a", run)])
    with pytest.raises(TypeError, match="ProjectAction"):
        Plugin(id="p", version="1", project_actions=[{"id": "a"}])
    with pytest.raises(TypeError, match="auto must be callable"):
        PanelSpec("k", client=True, auto="nope")
    plugin = Plugin(id="p", version="1", project_actions=[ProjectAction("a", run, mutating=True, timeout=300)])
    assert plugin.project_actions[0].timeout == 300
