"""Studio mailbox HTTP contract, continuation recovery and final output tails."""
import json

import pytest

from alfrd import chat
from alfrd.mailbox import Mailbox, USER
from alfrd.runtime import scheduler
from test_agent_mailbox import AGENT, answer, permission, question
from test_chat_orchestration import fake, plan, project, result, run, say, task  # noqa: F401


@pytest.fixture
def client(project, tmp_path):
    pytest.importorskip("flask")
    from alfrd.gui import create_app
    from alfrd.gui.services import RuntimeCatalogReader
    from alfrd.runtime import RuntimeService, RuntimeStore
    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    service = RuntimeService(store)
    service.register_manifest(project / ".alfrd" / "alfrd.yaml")
    app = create_app({"TESTING": True, "SECRET_KEY": "k", "RUNTIME_SERVICE": service,
                      "CATALOG_READER": RuntimeCatalogReader(service),
                      "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'catalog.sqlite'}"})
    client = app.test_client()
    client.headers = {"X-CSRF-Token": client.get("/api/studio/session").get_json()["csrf_token"]}
    return client


def setup(project):
    sid = chat.create_session(project)["id"]
    say(project, sid, "work")
    store = chat.session(project, sid)
    store.save_task({"row": "r0001.a", "request": "r0001", "kind": "task", "owner": "codex",
                     "task": {"id": "a"}, "units": [AGENT["unit_id"]]})
    base = f"/api/studio/projects/chatproj/chat/sessions/{sid}"
    return sid, base, Mailbox(project, sid, "r0001")


@pytest.mark.parametrize("outcome", ["approve", "deny"])
def test_mailbox_endpoints_bind_identity_and_survive_app_restart(project, client, outcome):
    sid, base, box = setup(project)
    route = base + "/requests/r0001/mailbox"
    request = box.send(AGENT, permission())["message"]
    assert client.get(route + "/pending").get_json()["messages"][0]["id"] == request["id"]
    payload = {"recipient": {"kind": "agent", "agent_id": "codex", "task_id": "a"}, "kind": "info",
               "subject": "Update", "body": "Keep going", "idempotency_key": "update", "payload": {"probe": "ping", "seq": 1}}
    assert client.post(route + "/messages", json=payload).status_code == 403  # CSRF
    first = client.post(route + "/messages", json=payload, headers=client.headers)
    assert first.status_code == 201
    assert first.get_json()["message"]["sender"] == USER
    assert client.post(route + "/messages", json=payload, headers=client.headers).status_code == 200
    forged = client.post(route + "/messages", json={**payload, "sender": AGENT}, headers=client.headers)
    assert forged.status_code == 400
    page = client.get(route + "/messages?limit=1").get_json()
    assert len(page["messages"]) == 1 and page["has_more"]
    next_page = client.get(route + "/messages", query_string={"after": page["next_cursor"]}).get_json()
    assert next_page["messages"][0]["subject"] == "Update"
    mid = request["id"]
    assert client.post(route + f"/messages/{mid}/ack", json={}, headers=client.headers).status_code == 200
    reply = {"idempotency_key": outcome, "expected_version": 1, "outcome": outcome,
             "action_hash": request["payload"]["action_hash"]}
    assert client.post(route + f"/messages/{mid}/reply", json=reply, headers=client.headers).status_code == 200
    assert client.post(route + f"/messages/{mid}/reply", json={**reply, "outcome": "deny" if outcome == "approve" else "approve"}, headers=client.headers).status_code == 409
    assert client.get(route + "/pending").get_json()["messages"] == []
    assert Mailbox(project, sid, "r0001").status(mid, AGENT)["reply"]["payload"]["outcome"] == outcome
    agent_inbox = Mailbox(project, sid, "r0001").list(AGENT)["messages"]
    assert next(m for m in agent_inbox if m["reply_to"] == mid)["payload"]["outcome"] == outcome
    # A new application instance obtains pending/resolution state entirely from disk.
    from alfrd.gui import create_app
    restarted = create_app(client.application.config).test_client()
    assert restarted.get(route + "/messages").get_json()["events"][-1]["type"] == "resolved"
    assert client.get(base + "/requests/r9999/mailbox/messages").status_code == 404
    assert client.get(route + "/messages?limit=no").status_code == 400
    assert client.get(route + "/messages?after=not-a-cursor").status_code == 400


def test_mailbox_http_validation_names_kind_and_field(project, client):
    _, base, _ = setup(project)
    route = base + "/requests/r0001/mailbox/messages"
    data = permission()
    data["payload"]["extra"] = "unexpected"
    response = client.post(route, json=data, headers=client.headers)
    assert response.status_code == 400
    error = response.get_json()["error"]["message"]
    assert "permission_request message field payload:" in error and "extra" in error
    del data["payload"]["extra"]
    del data["payload"]["action"]
    response = client.post(route, json=data, headers=client.headers)
    assert response.status_code == 400
    error = response.get_json()["error"]["message"]
    assert "permission_request message field payload:" in error and "action" in error


def test_activity_final_attempt_filters_bounds_and_display(project, client):
    fake(project, plan=plan(task("a")), a=result())
    sid = chat.create_session(project)["id"]
    say(project, sid, "work")
    run(project, sid)
    folder = scheduler.PlanDir(project, f"chat-{sid}")
    unit = next(u for u in folder.units() if u["row"] == "r0001.a")
    log = project / unit["log"]
    log.write_text("\n".join([f"line {i}" for i in range(20)]) + '\n' + json.dumps({"type": "unknown", "secret_arguments": {"token": "HIDDEN"}}) + '\n')
    base = f"/api/studio/projects/chatproj/chat/sessions/{sid}/activity"
    assert client.get(base).get_json()["running"] == []
    response = client.get(base, query_string={"units": unit["id"], "request": "r0001", "lines": 5})
    assert response.status_code == 200
    item = response.get_json()["items"][0]
    assert len(item["tail"]) == 5 and item["status"] != "running"
    assert item["display_tail"][-1] == "Additional technical activity recorded"
    assert "HIDDEN" not in json.dumps(item["display_tail"])
    assert item["cost_usd"] is None and item["cost_state"] == "unavailable"
    unit["total_cost_usd"] = 0.0
    folder.save_unit(unit)
    assert client.get(base, query_string={"units": unit["id"]}).get_json()["items"][0]["cost_state"] == "recorded"
    assert client.get(base, query_string={"units": "foreign"}).status_code == 404
    assert client.get(base, query_string={"units": ",".join([unit["id"]] * 33)}).status_code == 400
    assert client.get(base, query_string={"lines": "bad"}).status_code == 400
    log.unlink()
    assert client.get(base, query_string={"units": unit["id"]}).get_json()["items"][0]["tail_state"] == "missing"


def test_continuation_outbox_replay_and_mailbox_prompt(project):
    fake(project, plan=plan(task("a")), a=result("blocked", blockers=[{
        "id": "b1", "reason": "needs input", "needs": "user"}]))
    sid = chat.create_session(project)["id"]
    say(project, sid, "work")
    run(project, sid)
    store = chat.session(project, sid)
    unit = next(u for u in scheduler.PlanDir(project, store.plan_id).units() if u["row"] == "r0001.a")
    actor = {**AGENT, "unit_id": unit["id"]}
    box = Mailbox(project, sid, "r0001")
    mid = box.send(actor, question())["message"]["id"]
    d = store.decisions()[0]
    d["mailbox_message_id"] = mid
    store.save_decision(d)
    box.reply(mid, USER, answer())  # crash before delivery: no in-memory callback
    unit["status"] = "running"
    folder = scheduler.PlanDir(project, store.plan_id)
    folder.save_unit(unit)
    assert not chat.process_mailbox(store)
    assert store.decision(d["id"])["status"] == "pending"
    unit["status"] = "done"
    folder.save_unit(unit)
    assert chat.process_mailbox(store)
    # A crash before resume leaves the durable outbox pending, so delivery retries.
    assert chat.process_mailbox(store)
    chat.acknowledge_mailbox_delivery(store)
    assert not chat.process_mailbox(store)
    assert store.decision(d["id"])["status"] == "resolved"
    assert store.task("r0001.a")["status"] == "queued"
    fake(project, a=result())
    run(project, sid)
    attempts = [u for u in folder.units() if u["row"] == "r0001.a"]
    assert len(attempts) == 2
    prompt = (store.tasks_dir / "r0001.a" / "prompt.md").read_text()
    seq = box.status(mid, USER)["message"]["seq"]
    assert "<mailbox>" in prompt and f"#{seq} ASK high you→user resolved:answer" in prompt
    assert chat.MAILBOX_RULES in prompt
    context = folder.path / "mailbox" / "contexts" if hasattr(folder, "path") else project / ".alfrd" / "plans" / store.plan_id / "mailbox" / "contexts"
    assert list(context.glob("*.json"))
    state = chat.session_state(project, sid)
    assert state["mailbox_version"] == 1 and state["requests"][0]["mailbox"]["pending_user"] == 0


def test_blocker_link_validation_and_legacy_decision_bridge(project, monkeypatch):
    fake(project, plan=plan(task("a")), a=result("blocked", blockers=[{
        "id": "b1", "reason": "needs approval", "needs": "user"}]))
    sid = chat.create_session(project)["id"]
    say(project, sid, "work")
    original_response = chat._response
    posted = []

    def response(runner, unit):
        text = original_response(runner, unit)
        if unit["row"] == "r0001.a":
            box = Mailbox(project, sid, "r0001")
            actor = {**AGENT, "unit_id": unit["id"]}
            mail = box.send(actor, permission())["message"]
            posted.append(mail["id"])
            envelope = json.loads(text.split("```json\n")[1].split("\n```")[0])
            envelope["result"]["blockers"][0]["mailbox_message_id"] = mail["id"]
            return "```json\n" + json.dumps(envelope) + "\n```"
        return text

    monkeypatch.setattr(chat, "_response", response)
    run(project, sid)
    store = chat.session(project, sid)
    decision = store.decisions()[0]
    assert decision["mailbox_message_id"] == posted[0]
    resolved = chat.decide(project, sid, decision["id"], choice="deny", spawn=False)
    assert resolved["status"] == "resolved"
    assert Mailbox(project, sid, "r0001").status(posted[0], USER)["reply"]["payload"]["outcome"] == "deny"
    from alfrd import orchestration as proto
    with pytest.raises(proto.ProtocolError):
        proto.validate_mailbox_blocker({"mailbox_message_id": posted[0]}, store, "r0001", "foreign-unit")


def test_display_tail_suppresses_valid_envelopes_and_handles_malformed_events():
    from alfrd.orchestration import PROTOCOL
    envelope = {"protocol": PROTOCOL, "kind": "result", "result": {"status": "done", "summary": "Finished"}}
    text = "Done.\n```json\n" + json.dumps(envelope, indent=2) + "\n```"
    assert chat._display_log(json.dumps({"type": "result", "result": text})) == ["Done."]
    assert chat._display_log(json.dumps({"type": "usage", "usage": []})) == ["Usage recorded"]
    assert chat._display_log(json.dumps({"type": 2})) == ["Additional technical activity recorded"]
    code = 'Example:\n```json\n{"hello": "world"}\n```'
    assert chat.text_without_envelope(code) == code


def test_display_stream_deltas_join_and_hide_private_or_tool_data():
    events = [{"type": "stream_event", "event": {"delta": delta}} for delta in [
        {"type": "text_delta", "text": "Hello "},
        {"type": "thinking_delta", "thinking": "PRIVATE"},
        {"type": "input_json_delta", "partial_json": "SECRET"},
        {"type": "text_delta", "text": "world"}]]
    assert chat._display_log("\n".join(json.dumps(e) for e in events)) == ["Hello world"]
