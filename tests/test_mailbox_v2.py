"""Versioned mailbox records, per-request pinning and v2 metadata contracts."""
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from alfrd.mailbox import Mailbox, MailboxError, PROTOCOL, PROTOCOL_V1, USER, canonical, validate
from alfrd.mailbox_codes import CATEGORIES, CODES, PRIORITIES
from test_agent_mailbox import AGENT, answer, box, permission, question  # noqa: F401
from test_chat_orchestration import project, say  # noqa: F401


def legacy(box, explicit=False):
    req = box.session.request(box.run_id)
    if explicit:
        req["mailbox_protocol"] = PROTOCOL_V1
    else:
        req.pop("mailbox_protocol")
    box.session.save_request(req)
    return Mailbox(box.session.root, box.session.id, box.run_id)


def schema(name, version):
    import alfrd.mailbox
    path = Path(alfrd.mailbox.__file__).parent / "schemas" / f"{name}.{version}.json"
    return Draft202012Validator(json.loads(path.read_text()))


def test_new_request_pins_v2_before_first_mail(box):
    assert box.session.request(box.run_id)["mailbox_protocol"] == PROTOCOL
    assert not box.dir.exists()
    message = box.send(AGENT, question(kind="info", payload={}))["message"]
    assert message["protocol"] == PROTOCOL
    assert {k: message[k] for k in ("code", "category", "priority")} == {
        "code": "NTE", "category": "note", "priority": "low"}
    assert message["summary"] == message["subject"]
    assert message["description"] == "" and message["step_id"] is None
    schema("agent_message", "v2").validate(message)
    with pytest.raises(ValidationError):
        schema("agent_message", "v1").validate(message)
    box.ack(message["id"], USER)
    event = json.loads((box.dir / "events.jsonl").read_text())
    schema("agent_mailbox_event", "v2").validate(event)


@pytest.mark.parametrize("explicit", [False, True])
def test_empty_legacy_request_and_restart_keep_writing_v1(box, explicit):
    old = legacy(box, explicit)
    first = old.send(AGENT, question())["message"]
    again = Mailbox(box.session.root, box.session.id, box.run_id)
    assert again.send(AGENT, question())["message"] == first
    again.ack(first["id"], USER)
    reply = again.reply(first["id"], USER, answer())["message"]
    info = again.send(AGENT, question("note", kind="info", payload={}))["message"]
    for raw in (first, reply, info):
        assert raw["protocol"] == PROTOCOL_V1
        assert "code" not in raw
        schema("agent_message", "v1").validate(raw)
        validate("agent_message", raw)
    for line in (box.dir / "events.jsonl").read_text().splitlines():
        schema("agent_mailbox_event", "v1").validate(json.loads(line))
    projected = again.list()["messages"]
    assert projected[0]["code"] == "ASK"
    assert projected[1]["code"] == "REP"
    assert projected[1]["category"] == "question" and projected[1]["priority"] == "high"
    assert projected[2]["code"] == "NTE"
    assert again.status(first["id"], AGENT)["message"]["summary"] == first["subject"]
    with pytest.raises(MailboxError):
        again.send(AGENT, question("metadata", code="ASK"))
    # Read projections never rewrite authoritative v1 JSONL.
    assert all("code" not in json.loads(line) for line in (box.dir / "messages.jsonl").read_text().splitlines())


@pytest.mark.parametrize("code", list(CODES))
def test_code_table_and_defaults(box, code):
    row = CODES[code]
    assert row["category"] in CATEGORIES or code == "REP"
    assert row["priority"] in PRIORITIES or code == "REP"
    if code == "REP":
        parent = box.send(AGENT, question(priority="urgent"))["message"]
        message = box.reply(parent["id"], USER, answer())["message"]
        assert message["category"] == "question" and message["priority"] == "urgent"
    else:
        data = permission(code=code) if code == "PRM" else question(
            kind=row["kind"], code=code,
            payload={} if row["kind"] == "info" else question()["payload"])
        message = box.send(AGENT, data)["message"]
        assert message["category"] == row["category"]
        assert message["priority"] == row["priority"]
    assert message["code"] == code


@pytest.mark.parametrize("extra", [
    {"code": "PRM"}, {"code": "PRG", "category": "note"},
    {"category": "permission"}, {"code": "REP"}, {"code": "UNKNOWN"},
    {"priority": "critical"}, {"priority": 3}, {"state": "acked"},
    {"description": "x" * 2049}, {"step_id": "../other"},
])
def test_metadata_conflicts_are_rejected_without_append(box, extra):
    with pytest.raises(MailboxError):
        box.send(AGENT, question(kind="info", payload={}, **extra))
    assert box.list()["messages"] == []


def test_optional_metadata_and_category_only_defaults(box):
    data = question(kind="info", payload={}, category="result", priority="urgent",
                    summary="Ready", description="x" * 2048, step_id="step-1")
    first = box.send(AGENT, data)
    assert first["message"]["code"] == "DON"
    assert first["message"]["priority"] == "urgent"
    assert first["message"]["description"] == "x" * 2048
    assert first["message"]["step_id"] == "step-1"
    assert box.send(AGENT, data)["created"] is False
    raw = first["message"].copy()
    for key in ("code", "category", "priority", "summary", "description", "step_id"):
        del raw[key]
    schema("agent_message", "v2").validate(raw)
    assert box.send(AGENT, question("orchestrator", recipient={"kind": "orchestrator"}))["message"]["code"] == "ASO"


@pytest.mark.parametrize("v1", [False, True])
def test_acked_info_and_ack_does_not_resolve_or_approve(box, v1):
    if v1:
        box = legacy(box)
    info = box.send(AGENT, question(kind="info", payload={}))["message"]
    event = box.ack(info["id"], USER)
    assert box.ack(info["id"], USER) == event
    assert box.status(info["id"], AGENT)["state"] == "acked"
    assert box.list()["messages"][0]["state"] == "acked"
    for data in (question("question"), permission()):
        message = box.send(AGENT, data)["message"]
        box.ack(message["id"], USER)
        assert box.status(message["id"], AGENT)["state"] == "pending"
        assert box.status(message["id"], AGENT)["reply"] is None
    assert len(box.pending()["messages"]) == 2
    assert box.summary()["pending_user"] == 2


@pytest.mark.parametrize("v1", [False, True])
def test_exact_utf8_record_limit_and_oversized_write(box, v1):
    if v1:
        box = legacy(box)
    message = box.send(AGENT, question(kind="info", payload={}))["message"]
    raw = {**message, "payload": {"text": ""}}
    remaining = 32768 - len(canonical(raw).encode("utf-8"))
    raw["payload"]["text"] = "界" * (remaining // 3) + "x" * (remaining % 3)
    assert len(canonical(raw).encode("utf-8")) == 32768
    validate("agent_message", raw)
    raw["payload"]["text"] += "é"
    with pytest.raises(MailboxError, match="32 KiB") as error:
        validate("agent_message", raw)
    assert error.value.status == 413
    with pytest.raises(MailboxError, match="32 KiB"):
        box.send(AGENT, question("oversize", kind="info", payload={"text": "界" * 11000}))
    assert len(box.list()["messages"]) == 1


def test_v2_schema_rejects_code_kind_and_category_conflicts(box):
    message = box.send(AGENT, question())["message"]
    for changed in ({"code": "NTE"}, {"category": "note"}):
        with pytest.raises(ValidationError):
            schema("agent_message", "v2").validate({**message, **changed})
