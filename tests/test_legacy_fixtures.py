"""Fixed pre-v0.4 session and mailbox-v1 records remain readable and writable."""
from pathlib import Path
import json
import shutil

import pytest

from alfrd import chat
from alfrd.mailbox import Mailbox, PROTOCOL_V1, USER
from test_chat_orchestration import project  # noqa: F401


FIXTURES = Path(__file__).parent / "fixtures"
SID = "20260101-120000-abcdef"


@pytest.fixture
def legacy_store(project):
    destination = project / ".alfrd" / "chat" / SID
    shutil.copytree(FIXTURES / "legacy", destination)
    return chat.session(project, SID)


def snapshot(directory):
    return {p.relative_to(directory): p.read_bytes() for p in directory.rglob("*") if p.is_file()}


def assert_preserved(directory, original):
    for path, data in original.items():
        assert (directory / path).read_bytes() == data, path


def test_legacy_session_state_and_graph(legacy_store):
    store = legacy_store
    original = snapshot(store.dir)
    assert not store.rev_file.exists()
    assert "rev" not in store.load()
    assert "groups" not in json.loads(store.graph_file.read_text())
    assert not (store.dir / "attachments").exists()
    assert all("attachments" not in m for m in store.messages())
    assert "request" not in store.task("r0001.a")
    assert "created_ns" not in store.task("r0001.a")

    state = chat.session_state(store.root, SID)
    assert state["rev"] == 0
    assert state["session"]["title"] == "Legacy text-only session"
    assert [m["text"] for m in state["messages"]] == [
        "Inspect the legacy output", "Legacy output inspected."]
    assert state["requests"][0]["id"] == "r0001"
    assert state["requests"][0]["mailbox"]["pending_user"] == 0
    assert state["tasks"][0]["status"] == "done"
    assert state["tasks"][0]["attempts"] == 0
    graph = chat.graph(store)
    assert graph == state["graph"]
    assert graph["rev"] == 0
    assert graph["edges"] == []
    assert graph["nodes"][0]["request"] is None
    assert graph["nodes"][0]["status_text"]
    assert [g["id"] for g in graph["groups"]] == ["r0001", "unscoped"]
    assert graph["groups"][-1]["nodes"] == ["r0001.a"]
    assert graph["groups"][-1]["counts"]["total"] == 1
    assert chat.session_state(store.root, SID, since_rev=0) == {"rev": 0, "unchanged": True}
    assert_preserved(store.dir, original)
    assert not store.rev_file.exists()


@pytest.fixture(params=[False, True], ids=["implicit-v1", "explicit-v1"])
def v1_box(legacy_store, request):
    store = legacy_store
    source = FIXTURES / "mailbox_v1"
    record = json.loads((source / "request.json").read_text())
    if request.param:
        record["mailbox_protocol"] = PROTOCOL_V1
    # Install historical records directly; current save methods would bump rev.
    (store.requests_dir / "r0001.json").write_text(json.dumps(record))
    box = Mailbox(store.root, SID, "r0001")
    box.dir.mkdir(parents=True)
    for name in ("messages.jsonl", "events.jsonl"):
        shutil.copyfile(source / name, box.dir / name)
    return box


def test_v1_transcript_replay_and_defaults(v1_box):
    box = v1_box
    original = snapshot(box.dir)
    assert box.protocol == PROTOCOL_V1
    messages = box.list()["messages"]
    assert [m["code"] for m in messages] == ["ASK", "REP", "NTE", "ASK"]
    assert [m["state"] for m in messages] == ["resolved", "pending", "acked", "pending"]
    assert messages[1]["category"] == "question"
    assert messages[1]["priority"] == "high"
    assert all(m["summary"] == m["subject"] and m["description"] == "" and m["step_id"] is None
               for m in messages)
    assert [m["id"] for m in box.pending(USER)["messages"]] == ["msg_legacy_4"]
    assert box.status("msg_legacy_1", USER)["reply"]["id"] == "msg_legacy_2"
    state = chat.session_state(box.session.root, SID)
    assert state["requests"][0]["mailbox"]["pending_user"] == 1
    assert state["rev"] == state["graph"]["rev"] == 0
    assert_preserved(box.dir, original)
    restarted = Mailbox(box.session.root, SID, "r0001")
    assert restarted.list()["messages"] == messages


def test_v1_transcript_continues_writing_v1(v1_box):
    box = v1_box
    old_messages = (box.dir / "messages.jsonl").read_bytes()
    reply = box.reply("msg_legacy_4", USER, {
        "idempotency_key": "fixture-answer", "outcome": "answer", "choice_id": "yes"})["message"]
    assert reply["protocol"] == PROTOCOL_V1 and reply["seq"] == 5
    assert "code" not in reply
    restarted = Mailbox(box.session.root, SID, "r0001")
    assert restarted.protocol == PROTOCOL_V1
    assert restarted.status("msg_legacy_4", USER)["state"] == "resolved"
    assert restarted.pending(USER)["messages"] == []
    assert (box.dir / "messages.jsonl").read_bytes().startswith(old_messages)
    for name in ("messages.jsonl", "events.jsonl"):
        records = [json.loads(line) for line in (box.dir / name).read_text().splitlines()]
        assert all(r["protocol"] == PROTOCOL_V1 and "code" not in r for r in records)
