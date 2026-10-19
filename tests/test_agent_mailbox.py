"""Mailbox persistence, writer serialization, scope and approval semantics."""
import json
from concurrent.futures import ProcessPoolExecutor

import pytest
from typer.testing import CliRunner

from alfrd import chat
from alfrd.cli import alfrd_cli
from alfrd.mailbox import Mailbox, MailboxError, USER, action_hash, execution_env
from test_chat_orchestration import project, say  # noqa: F401

AGENT = {"kind": "agent", "agent_id": "codex", "task_id": "a", "unit_id": "0002-r0001.a-codex"}


@pytest.fixture
def box(project):
    sid = chat.create_session(project)["id"]
    say(project, sid, "work")
    store = chat.session(project, sid)
    store.save_task({"row": "r0001.a", "request": "r0001", "kind": "task", "owner": "codex",
                     "task": {"id": "a"}, "units": [AGENT["unit_id"]]})
    return Mailbox(project, sid, "r0001")


def question(key="q", **extra):
    return {"recipient": USER, "kind": "question", "subject": "Choose", "body": "Which one?",
            "idempotency_key": key, "payload": {"options": [{"id": "a", "label": "A"}], "allow_text": True}, **extra}


def permission(key="p", **extra):
    payload = {"action": "publish", "target": "preview", "description": "Publish preview", "scope": "once",
               "choices": ["approve", "deny"], "arguments": {"revision": "abc"}}
    payload["action_hash"] = action_hash(payload)
    return question(key, kind="permission_request", payload=payload, **extra)


def answer(key="reply", **extra):
    return {"idempotency_key": key, "outcome": "answer", "answer": "yes", **extra}


def test_restart_cursor_idempotency_and_ack(box):
    first = box.send(AGENT, question())
    again = Mailbox(box.session.root, box.session.id, box.run_id)
    assert again.send(AGENT, question())["message"] == first["message"]
    assert again.send(AGENT, question())["created"] is False
    with pytest.raises(MailboxError) as conflict:
        again.send(AGENT, question(body="different"))
    assert conflict.value.status == 409
    page = again.list(limit=1)
    assert len(page["messages"]) == 1
    assert again.list(after=page["next_cursor"])["messages"] == []
    mid = first["message"]["id"]
    event = again.ack(mid, USER)
    assert again.ack(mid, USER) == event
    assert again.pending()["messages"][0]["state"] == "pending"
    assert again.list(after=page["next_cursor"])["events"][0]["type"] == "ack"
    with pytest.raises(MailboxError):
        again.list(after="invalid")


def test_permission_recipient_exact_hash_and_first_reply_wins(box):
    request = box.send(AGENT, permission())["message"]
    reply = answer(outcome="approve", answer=None, action_hash=request["payload"]["action_hash"])
    with pytest.raises(MailboxError) as forbidden:
        box.reply(request["id"], AGENT, reply)
    assert forbidden.value.status == 403
    with pytest.raises(MailboxError):
        box.reply(request["id"], USER, {**reply, "action_hash": "sha256:" + "0" * 64})
    with pytest.raises(MailboxError) as stale:
        box.reply(request["id"], USER, {**reply, "expected_version": 8})
    assert stale.value.status == 409
    saved = box.reply(request["id"], USER, reply)
    assert saved == box.reply(request["id"], USER, reply)
    with pytest.raises(MailboxError) as conflict:
        box.reply(request["id"], USER, {**reply, "idempotency_key": "other", "outcome": "deny"})
    assert conflict.value.status == 409
    assert box.pending()["messages"] == []
    assert box.status(request["id"], AGENT)["reply"]["payload"]["outcome"] == "approve"


def test_expiry_cancellation_and_reply_crash_repair(box):
    mid = box.send(AGENT, question(expires_at="2000-01-01T00:00:00Z"))["message"]["id"]
    with pytest.raises(MailboxError) as expired:
        box.reply(mid, USER, answer())
    assert expired.value.details["state"] == "expired"
    mid = box.send(AGENT, question("next"))["message"]["id"]
    box.cancel("a")
    assert box.status(mid, AGENT)["state"] == "cancelled"
    mid = box.send(AGENT, question("last"))["message"]["id"]
    box.reply(mid, USER, answer())
    events = box.dir / "events.jsonl"
    events.write_bytes(b"\n".join(events.read_bytes().splitlines()[:-1]) + b"\n")
    recovered = Mailbox(box.session.root, box.session.id, box.run_id)
    assert recovered.status(mid, AGENT)["state"] == "resolved"


def test_partial_trailing_record_recovered_complete_corruption_fails(box):
    box.send(AGENT, question())
    path = box.dir / "messages.jsonl"
    with path.open("ab") as stream:
        stream.write(b'{"id":')
    assert box.send(AGENT, question("next"))["message"]["seq"] == 2
    with path.open("ab") as stream:
        stream.write(b'{}\n')
    with pytest.raises(MailboxError) as corrupt:
        box.list()
    assert corrupt.value.status == 500


def _write(args):
    root, sid, key = args
    return Mailbox(root, sid, "r0001").send(AGENT, question(key))["message"]["seq"]


def test_concurrent_process_writers(box):
    with ProcessPoolExecutor(max_workers=4) as pool:
        seqs = list(pool.map(_write, [(box.session.root, box.session.id, str(i)) for i in range(12)]))
    assert sorted(seqs) == list(range(1, 13))
    assert [m["seq"] for m in box.list()["messages"]] == list(range(1, 13))


def test_validation_visibility_and_context_cli(box, monkeypatch, tmp_path):
    with pytest.raises(MailboxError):
        box.send(AGENT, question(sender=USER))
    with pytest.raises(MailboxError):
        box.send(AGENT, {**permission(), "payload": {}})
    with pytest.raises(MailboxError):
        box.send(AGENT, question(body="x" * 16385))
    with pytest.raises(MailboxError):
        Mailbox(box.session.root, "..", "r0001")
    request = box.send(AGENT, question())["message"]
    other = {**AGENT, "agent_id": "sonnet", "task_id": "b"}
    assert box.list(other)["messages"] == []
    with pytest.raises(MailboxError):
        box.reply(request["id"], other, answer())
    env = execution_env(box.session, box.session.task("r0001.a"), {"id": AGENT["unit_id"]})
    monkeypatch.setenv("ALFRD_MAILBOX_CONTEXT", env["ALFRD_MAILBOX_CONTEXT"])
    cli = CliRunner()
    assert cli.invoke(alfrd_cli, ["mailbox", "read"]).exit_code == 0
    denied = cli.invoke(alfrd_cli, ["mailbox", "reply", request["id"], "--key", "spoof", "--choice-id", "a"])
    assert denied.exit_code == 1 and json.loads(denied.stdout)["status"] == 403
    pending = cli.invoke(alfrd_cli, ["mailbox", "wait", request["id"], "--timeout", "0"])
    assert pending.exit_code == 2
    box.reply(request["id"], USER, answer())
    assert cli.invoke(alfrd_cli, ["mailbox", "wait", request["id"], "--timeout", "0"]).exit_code == 0
    body = tmp_path / "body.txt"
    body.write_text("Hello")
    sent = cli.invoke(alfrd_cli, ["mailbox", "send", "--to", "user", "--subject", "Hello", "--body-file", str(body), "--key", "hello"])
    assert sent.exit_code == 0, sent.output
    assert json.loads(sent.stdout)["message"]["sender"] == AGENT


def test_approval_cannot_outlive_expiry_or_cancellation(box, monkeypatch):
    from datetime import datetime, timezone
    from alfrd import mailbox
    request = box.send(AGENT, permission(expires_at="2099-01-01T00:00:00Z"))["message"]
    approval = answer(outcome="approve", answer=None, action_hash=request["payload"]["action_hash"])
    box.reply(request["id"], USER, approval)

    class Future(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2100, 1, 1, tzinfo=timezone.utc)

    monkeypatch.setattr(mailbox, "datetime", Future)
    assert box.status(request["id"], AGENT)["state"] == "expired"
    with pytest.raises(MailboxError) as expired:
        box.reply(request["id"], USER, approval)
    assert expired.value.status == 409
    request = box.send(AGENT, permission("cancel-me"))["message"]
    box.reply(request["id"], USER, {**approval, "idempotency_key": "another"})
    box.cancel("a")
    assert box.status(request["id"], AGENT)["state"] == "cancelled"
