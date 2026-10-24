"""M2: event index, summary cache, 2,000-message benchmark and rebuild paths."""
import json
import time

from alfrd.mailbox import Mailbox, USER, canonical
from test_agent_mailbox import AGENT, answer, box, question  # noqa: F401
from test_chat_orchestration import project  # noqa: F401

COUNT = 2000
# Plan C10 treats a poll slower than 300 ms as the threshold for action.
POLL_BUDGET = 0.3


def seed(box, count=COUNT):
    """Write count messages (half info, acked; half pending questions) straight to the JSONL."""
    info = box.send(AGENT, question("i0", kind="info", payload={}))["message"]
    pending = box.send(AGENT, question("q0"))["message"]
    box.ack(info["id"], USER)
    event = json.loads((box.dir / "events.jsonl").read_text().splitlines()[0])
    messages, events = [], []
    for n in range(1, count + 1):
        base = info if n % 2 else pending
        messages.append({**base, "id": f"msg_{n:06d}", "seq": n, "idempotency_key": f"k{n}"})
        if n % 2:
            events.append({**event, "event_seq": len(events) + 1, "message_id": f"msg_{n:06d}"})
    (box.dir / "messages.jsonl").write_text("".join(canonical(m) + "\n" for m in messages))
    (box.dir / "events.jsonl").write_text("".join(canonical(e) + "\n" for e in events))
    (box.dir / "state.json").unlink(missing_ok=True)
    return messages, events


def fresh(box):
    return Mailbox(box.session.root, box.session.id, box.run_id)


def test_summary_benchmark_2000_messages(box):
    seed(box)
    reader = fresh(box)
    started = time.perf_counter()
    first = reader.summary()
    cold = time.perf_counter() - started  # one full validated replay per process
    assert first["pending_user"] == COUNT // 2
    started = time.perf_counter()
    again = reader.summary()
    cached = time.perf_counter() - started
    assert again == first
    # A poll after one new append replays only the appended record.
    reader.send(AGENT, question("late"))
    started = time.perf_counter()
    polled = fresh(box).summary()
    incremental = time.perf_counter() - started
    assert polled["pending_user"] == COUNT // 2 + 1
    assert cold < 10 * POLL_BUDGET, f"cold summary {cold:.3f}s"
    assert incremental < POLL_BUDGET, f"incremental summary {incremental:.3f}s"
    assert cached < 0.02, f"cached summary {cached:.3f}s"


def test_state_lookup_does_not_scan_events(box):
    messages, _ = seed(box)
    reader = fresh(box)
    with reader.locked():
        class Trap(list):
            def __iter__(self):
                raise AssertionError("events rescanned")
        reader.events = Trap(reader.events)
        assert reader._state(reader._find("msg_000001"))["state"] == "acked"
        assert reader._state(reader._find("msg_000002"))["state"] == "pending"


def test_summary_cache_invalidates_on_append(box):
    box.send(AGENT, question("a"))
    assert box.summary()["pending_user"] == 1
    box.send(AGENT, question("b"))
    assert box.summary()["pending_user"] == 2
    assert fresh(box).summary()["pending_user"] == 2


def test_rebuild_after_torn_append(box):
    sent = box.send(AGENT, question("a"))["message"]
    with open(box.dir / "events.jsonl", "ab") as stream:
        stream.write(b'{"event_seq": 1, "type": "re')
    with open(box.dir / "messages.jsonl", "ab") as stream:
        stream.write(b'{"seq": 2')
    reader = fresh(box)
    assert reader.summary()["pending_user"] == 1
    assert (box.dir / "messages.jsonl").read_bytes().endswith(b"\n")
    assert (box.dir / "events.jsonl").read_bytes() == b""
    reader.reply(sent["id"], USER, answer())
    assert fresh(box).summary()["pending_user"] == 0


def test_rebuild_after_deleted_checkpoint(box):
    first = box.send(AGENT, question("a"))["message"]
    box.send(AGENT, question("b"))
    box.reply(first["id"], USER, answer())
    expected = box.summary()
    (box.dir / "state.json").unlink()
    reader = fresh(box)
    assert reader.summary() == expected
    assert reader._state(reader._find(first["id"]))["state"] == "resolved"
    reader.send(AGENT, question("c"))
    assert json.loads((box.dir / "state.json").read_text()) == {"messages": 4, "events": 1}
