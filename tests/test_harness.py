"""Minimal synthetic records, including damaged input and historical attempts."""
import json
from datetime import datetime, timezone

import pytest

from alfrd import harness

SID = "20261101-120000-abcdef"
T0 = "2026-11-01T12:00:00Z"


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


@pytest.fixture
def run(tmp_path):
    store = tmp_path / ".alfrd/chat" / SID
    plan = tmp_path / ".alfrd/plans" / ("chat-" + SID)
    write(store / "session.json", {"id": SID, "title": "Synthetic", "created": T0})
    write(store / "requests/r0001.json", {"id": "r0001", "created": T0, "status": "working"})
    for row, agent, dependencies in [("r0001.design", "claude", []), ("r0001.build", "codex", ["r0001.design"])]:
        write(store / "tasks" / row / "task.json", {"row": row, "request": "r0001", "kind": "task", "owner": agent,
            "created": T0, "status": "running", "depends_on": dependencies, "task": {"title": row}})
    write(plan / "units/u1.json", {"id": "u1", "row": "r0001.build", "agent": "codex", "started": T0,
        "finished": "2026-11-01T12:10:00Z", "status": "done", "agent_usage": {"total_tokens": 123}, "total_cost_usd": 1.5})
    events = [
        {"seq": 1, "at": T0, "kind": "task.created", "agent": "claude", "target": "r0001.design"},
        {"seq": 2, "at": T0, "kind": "task.finished", "agent": "claude", "target": "r0001.design", "data": {"status": "done"}},
        {"seq": 3, "at": T0, "kind": "turn.started", "agent": "codex", "unit": "u1", "target": "r0001.build"},
        {"seq": 4, "at": "2026-11-01T12:10:00Z", "kind": "turn.finished", "agent": "codex", "unit": "u1", "target": "r0001.build", "data": {"status": "done"}},
    ]
    plan.mkdir(parents=True, exist_ok=True)
    (plan / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n{broken\n[]\n")
    return tmp_path, store, plan


def test_config_defaults_and_isolation(tmp_path):
    assert harness.settings(tmp_path) == harness.DEFAULTS
    assert harness.merge_settings({"stuck_after_minutes": 0.01, "poll_seconds": 3})["stuck_after_minutes"] == 0.01
    for raw in (None, [], {"poll_seconds": True, "stuck_after_minutes": float("nan"), "role_order": 2}):
        assert harness.merge_settings(raw) == harness.DEFAULTS
    changed = harness.merge_settings()
    changed["role_order"].append("other")
    assert "other" not in harness.DEFAULTS["role_order"]


def test_config_file_and_missing_keys(tmp_path):
    (tmp_path / "alfrd.yaml").write_text("name: test\nharness:\n  default_project: sample\n  poll_seconds: 4\n")
    conf = harness.settings(tmp_path)
    assert conf["poll_seconds"] == 4 and conf["default_project"] == "sample"
    assert conf["stuck_after_minutes"] == 10
    (tmp_path / "alfrd.yaml").write_text("name: test\n")
    assert harness.settings(tmp_path) == harness.DEFAULTS


def test_events_rotation_broken_lines_and_cache(run):
    _, _, plan = run
    path = plan / "events.jsonl"
    rotated = path.with_name("events.jsonl.1")
    rotated.write_text('{"seq":0,"at":"2026-11-01T11:59:00Z","kind":"plan.started"}\nnull\n')
    assert [e["seq"] for e in harness.read_events(path)] == [1, 2, 3, 4]  # seq 0 is outside EventLog's cursor
    path.write_text('{"seq":9,"at":"2026-11-01T12:00:00Z","kind":"task.started","data":[]}\n')
    assert harness.read_events(path)[0]["data"] == {}
    path.unlink()
    assert harness.read_events(path) == []


def test_rebuild_replay_stuck_usage_and_feed_scope(run, monkeypatch):
    root, _, _ = run
    monkeypatch.setattr(harness, "settings", lambda _: harness.merge_settings({"stuck_after_minutes": 0.5}))
    before = harness.state(root, SID, scope="request", request="r0001", at="2026-11-01T11:00:00Z")
    assert not before["agents"] and not before["timeline_segments"]
    state = harness.state(root, SID, scope="request", request="r0001", at="2026-11-01T12:01:00Z", limit=1)
    assert state["summary"]["stuck"] == 1
    assert state["timeline_segments"][0]["open"]
    assert state["summary"]["usage"]["total_tokens"] is None
    assert state["handoff_edges"][0]["status"] == "available"
    assert len(state["feed_events"]) == 1 and state["feed_page"]["has_more"]
    page2 = harness.state(root, SID, scope="request", request="r0001", at="2026-11-01T12:01:00Z", before_seq=3)
    assert page2["summary"] == state["summary"]
    done = harness.state(root, SID, scope="all", at="2026-11-01T12:11:00Z")
    assert not done["timeline_segments"][0]["open"] and done["summary"]["stuck"] == 0
    assert done["summary"]["usage"]["total_tokens"] == 123
    assert done["summary"]["usage"]["total_cost_usd"] == 1.5
    assert done["coverage"]["complete"] is False


def test_bad_records_and_validation(run):
    root, store, plan = run
    (store / "tasks/bad").mkdir()
    (store / "tasks/bad/task.json").write_text("{broken")
    (plan / "units/bad.json").write_text("[1]")
    result = harness.state(root, SID, scope="all", at="2999-01-01T00:00:00Z", now=datetime(2026, 11, 2, tzinfo=timezone.utc))
    assert result["clock"]["at"] == "2026-11-02T00:00:00Z"
    for options in ({"at": "2026-11-01T12:00:00"}, {"scope": "request"}, {"scope": "other"}, {"scope": "all", "request": "r0001"}, {"limit": 0}, {"before_seq": -1}):
        with pytest.raises(ValueError):
            harness.state(root, SID, **options)
    with pytest.raises(LookupError):
        harness.state(root, SID, scope="request", request="../../missing")


def test_no_writes(run, monkeypatch):
    root, _, _ = run
    before = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    monkeypatch.setattr("alfrd.chat.check", lambda *a, **k: pytest.fail("recovery invoked"))
    harness.state(root, SID, scope="all")
    assert before == {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_working_without_unit_and_formal_handoff(run, monkeypatch):
    root, _, plan = run
    monkeypatch.setattr(harness, "settings", lambda _: harness.merge_settings({"stuck_after_minutes": 0.5}))
    (plan / "units/u1.json").unlink()
    events = harness.read_events(plan / "events.jsonl")[:2]
    events += [{"seq": 3, "at": T0, "kind": "handoff.published", "agent": "claude", "target": "r0001.design"}]
    (plan / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events))
    state = harness.state(root, SID, scope="all", now=datetime(2026, 11, 1, 12, 1, tzinfo=timezone.utc))
    assert next(a for a in state["agents"] if a["id"] == "codex")["status"] == "stuck"
    assert {e["kind"] for e in state["handoff_edges"]} == {"depends_on", "published"}


def test_timestamped_provider_progress_is_read_only_and_time_filtered(run, monkeypatch):
    root, _, plan = run
    monkeypatch.setattr(harness, "settings", lambda _: harness.merge_settings({"stuck_after_minutes": 0.5}))
    log = plan / "logs/u1.events.jsonl"
    log.parent.mkdir()
    log.write_text('{"type":"assistant","timestamp":"2026-11-01T12:00:50Z","content":"private"}\n{bad\n')
    current = harness.state(root, SID, scope="all", at="2026-11-01T12:01:00Z")
    assert current["summary"]["stuck"] == 0
    historical = harness.state(root, SID, scope="all", at="2026-11-01T12:00:40Z")
    assert historical["summary"]["stuck"] == 1
    assert "private" not in json.dumps(current)


def test_shared_view_graph_messages_and_decision_history(run):
    root, store, _ = run
    (store / "messages.jsonl").write_text("\n".join(json.dumps(m) for m in [
        {"id": "m1", "at": T0, "role": "user", "text": "earlier", "request": "r0001"},
        {"id": "m2", "at": "2026-11-01T13:00:00Z", "role": "assistant", "text": "later", "request": "r0001"}]) + "\n{bad")
    write(store / "decisions/d1.json", {"id": "d1", "created": T0, "request": "r0001", "status": "resolved",
        "resolved": "2026-11-01T13:00:00Z", "question": "Continue?", "answer": "future"})
    view = harness.state(root, SID, scope="all", at="2026-11-01T12:01:00Z")["view"]
    assert view["graph"]["nodes"][0]["id"] == "r0001.build"
    assert view["graph"]["edges"][0]["from"] == "r0001.design"
    assert [m["id"] for m in view["messages"]] == ["m1"]
    assert view["blockers"][0]["status"] == "pending"
    assert "future" not in json.dumps(view)


def test_concurrent_attempts_retries_and_cumulative_usage(run, monkeypatch):
    root, _, plan = run
    monkeypatch.setattr(harness, "settings", lambda _: harness.merge_settings({"stuck_after_minutes": 0.5}))
    path = plan / "events.jsonl"
    events = harness.read_events(path)[:3]
    def event(seq, at, kind, unit, **data):
        return {"seq": seq, "at": f"2026-11-01T12:{at}Z", "kind": kind, "unit": unit,
                "agent": "codex", "target": "r0001.build", "data": data}
    events += [event(4, "00:01", "turn.started", "u2"),
        event(5, "00:10", "usage.updated", "u1", usage={"total_tokens": 10}),
        event(6, "00:20", "usage.updated", "u1", usage={"total_tokens": 20}),
        event(7, "00:50", "usage.updated", "u1", usage={"total_tokens": 20}),
        event(8, "00:55", "runner.heartbeat", "u1")]
    path.write_text("\n".join(json.dumps(e) for e in events))
    state = harness.state(root, SID, scope="all", at="2026-11-01T12:01:00Z")
    assert len(state["timeline_segments"]) == 2
    codex = next(a for a in state["agents"] if a["id"] == "codex")
    assert codex["status"] == "stuck" and len(codex["units"]) == 2
    assert codex["last_activity_at"] == "2026-11-01T12:00:20Z"
    assert codex["usage"]["total_tokens"] == 20  # cumulative samples replace
    events += [event(9, "01:10", "turn.failed", "u1"), event(10, "01:11", "turn.started", "u3")]
    path.write_text("\n".join(json.dumps(e) for e in events))
    state = harness.state(root, SID, scope="all", at="2026-11-01T12:01:20Z")
    assert len(state["timeline_segments"]) == 3
    assert next(s for s in state["timeline_segments"] if s["unit"] == "u1")["state"] == "failed"


@pytest.mark.parametrize("replay", [False, True])
@pytest.mark.parametrize("text,bounds,mode,title,budget", [
    ("\n  Build  a game  \nignored", {"max_cost_usd": 30}, "direct", "Build a game", 30),
    ("x" * 81, {"max_cost_usd": 1.5}, "orchestrated", "x" * 79 + "…", 1.5),
    (" \n", None, None, None, None),
    ("Title", {}, "unknown", "Title", None),
    ("Title", {"max_cost_usd": True}, None, "Title", None),
    ("Title", {"max_cost_usd": float("inf")}, None, "Title", None),
    ("Title", {"max_cost_usd": -1}, None, "Title", None),
    ("Title", {"max_cost_usd": 0}, None, "Title", None),
])
def test_request_title_budget_mode(run, replay, text, bounds, mode, title, budget):
    root, store, _ = run
    record = {"id": "r0001", "created": T0, "status": "done", "text": text, "mode": mode}
    if bounds is not None:
        record["bounds"] = bounds
    write(store / "requests/r0001.json", record)
    r = harness.state(root, SID, scope="all", **({"at": T0} if replay else {}))["view"]["requests"][0]
    assert (r["title"], r["max_cost_usd"], r["mode"]) == (title, budget, mode if mode in ("direct", "orchestrated") else None)
    assert r["status"] == ("unknown" if replay else "done")


def detail_record(store):
    path = store / "tasks/r0001.build/task.json"
    record = json.loads(path.read_text())
    record["task"].update(id="build", role="development", description="Build it",
        acceptance_criteria=["Works"], expected_outputs=[{"path": "game.py"}], budget={"max_retries": 3}, secret="hidden")
    record.update(result={"status": "done", "summary": "Finished", "artifacts": ["game.py"], "secret": "hidden"},
                  error="Late error", cost_warning={"cost": 2}, attempts=99, processed="hidden")
    write(path, record)
    return record


def test_task_detail_allowlist_live(run):
    root, store, plan = run
    record = detail_record(store)
    path = plan / "units/u1.json"
    unit = json.loads(path.read_text())
    unit.update(log=".alfrd/plans/log.txt", requested_model="model-fallback")
    write(path, unit)
    task = next(t for t in harness.state(root, SID, scope="all")["view"]["tasks"] if t["row"] == "r0001.build")
    assert task["task"] == {k: v for k, v in record["task"].items() if k != "secret"}
    assert task["result"] == {k: v for k, v in record["result"].items() if k != "secret"}
    assert task["error"] == "Late error" and task["cost_warning"] == {"cost": 2}
    assert task["attempts"] == 1 and task["usage"]["total_cost_usd"] == 1.5
    assert task["logs"] == [{"unit": "u1", "log": unit["log"], "status": "done", "model": "model-fallback",
        "started": T0, "finished": unit["finished"], "total_cost_usd": 1.5,
        "handoff": f".alfrd/plans/chat-{SID}/handoffs/u1"}]
    assert "hidden" not in json.dumps(task)


def test_task_detail_replay_omits_outcomes(run):
    root, store, plan = run
    detail_record(store)
    write(plan / "units/u2.json", {"id": "u2", "row": "r0001.build", "started": "2026-11-01T13:00:00Z"})
    state = harness.state(root, SID, scope="all", at="2026-11-01T12:01:00Z")
    task = next(t for t in state["view"]["tasks"] if t["row"] == "r0001.build")
    assert task["attempts"] == 1
    assert task["task"]["description"] == "Build it"
    assert not {"result", "logs", "error", "cost_warning"} & task.keys()
    assert task["usage"]["total_cost_usd"] is None
    assert task["latest"]["kind"] == "turn.started"


def test_task_latest_before_pagination(run):
    root, _, plan = run
    events = harness.read_events(plan / "events.jsonl")
    events += [{"seq": 5, "at": "2026-11-01T12:11:00Z", "kind": "usage.updated", "unit": "u1", "data": {"usage": {"total_tokens": 234}}},
               {"seq": 6, "at": "2026-11-01T12:12:00Z", "kind": "runner.heartbeat", "unit": "u1"}]
    (plan / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events))
    state = harness.state(root, SID, scope="all", limit=1)
    tasks = {t["row"]: t for t in state["view"]["tasks"]}
    assert len(state["feed_events"]) == 1
    assert tasks["r0001.design"]["latest"] == {"kind": "task.finished", "at": T0, "status": "done"}
    assert tasks["r0001.build"]["latest"]["kind"] == "turn.finished"
    earlier = harness.state(root, SID, scope="all", at="2026-11-01T12:01:00Z", limit=1)
    assert next(t for t in earlier["view"]["tasks"] if t["row"] == "r0001.build")["latest"]["kind"] == "turn.started"


def decision_record(store, **changes):
    record = {"id": "d1", "request": "r0001", "task": "r0001.build", "created": T0,
        "status": "resolved", "needs": "user", "kind": "question", "mailbox_message_id": "m1",
        "repeat": True, "delegated": True, "choice": "yes", "answer": "Future answer",
        "decided_by": "user", "resolved": "2026-11-01T13:00:00Z", **changes}
    write(store / f"decisions/{record['id']}.json", record)
    return record


def test_decision_linkage_fields(run):
    root, store, _ = run
    record = decision_record(store)
    view = harness.state(root, SID, scope="all")["view"]
    assert view["decisions"] == [record]
    assert view["blockers"] == []


@pytest.mark.parametrize("resolved", [None, "2026-11-01T13:00:00Z"])
def test_decision_replay_masks_resolution(run, resolved):
    root, store, _ = run
    decision_record(store, resolved=resolved)
    view = harness.state(root, SID, scope="all", at="2026-11-01T12:01:00Z")["view"]
    decision = view["decisions"][0]
    assert decision["status"] == "pending" and decision["mailbox_message_id"] == "m1"
    assert not {"choice", "answer", "decided_by", "resolved"} & decision.keys()
    if resolved:
        later = harness.state(root, SID, scope="all", at="2026-11-01T14:00:00Z")["view"]["decisions"][0]
        assert later["answer"] == "Future answer" and later["status"] == "resolved"


def attention_mailbox(run):
    from alfrd.mailbox import Mailbox
    from test_agent_mailbox import AGENT, permission, question
    root, store, _ = run
    write(store / "requests/r0002.json", {"id": "r0002", "created": T0, "status": "running"})
    write(store / "requests/r0001.json", {"id": "r0001", "created": T0, "status": "running"})
    box = Mailbox(root, SID, "r0001")
    task_path = store / "tasks/r0001.build/task.json"
    task = json.loads(task_path.read_text())
    task["units"] = ["u1"]
    write(task_path, task)
    actor = {**AGENT, "task_id": "build", "unit_id": "u1"}
    first = box.send(actor, question())["message"]
    second = box.send(actor, permission())["message"]
    other = Mailbox(root, SID, "r0002").send(AGENT, question("other"))["message"]
    return box, first, second, other


def test_attention_parity_with_list_sessions(run):
    from alfrd import chat
    root, store, _ = run
    _, first, second, other = attention_mailbox(run)
    decision_record(store, status="pending", mailbox_message_id=first["id"])
    decision_record(store, id="d2", status="pending", needs="orchestrator", mailbox_message_id=None)
    decision_record(store, id="d3", status="pending", mailbox_message_id=None, repeat=True)
    decision_record(store, id="d4", status="pending", mailbox_message_id=None, delegated=True, request="r0002")
    view = harness.state(root, SID, scope="all")["view"]
    attention = view["attention"]
    assert attention["count"] == chat.list_sessions(root)[0]["pending_user"] == 5
    assert attention["mailbox_known"] is True
    assert attention["items"][0]["id"] == second["id"]
    linked = next(i for i in attention["items"] if i["id"] == first["id"])
    assert linked["decision"] == "d1" and linked["at"] == first["created_at"]
    assert {i["id"] for i in attention["items"]} == {first["id"], second["id"], other["id"], "d3", "d4"}
    assert "body" not in json.dumps(attention) and "question" not in linked
    scoped = harness.state(root, SID, scope="request", request="r0002")["view"]["attention"]
    assert scoped["count"] == 2 and all(i["request"] == "r0002" for i in scoped["items"])
    assert len(view["blockers"]) == 4  # compatibility: includes orchestrator-owned


def test_attention_replay_decisions_only(run, monkeypatch):
    from alfrd.mailbox import Mailbox
    root, store, _ = run
    attention_mailbox(run)
    decision_record(store)
    decision_record(store, id="d2", needs="orchestrator")
    monkeypatch.setattr(Mailbox, "summary", lambda *a: pytest.fail("replay consulted mailbox"))
    attention = harness.state(root, SID, scope="all", at="2026-11-01T12:01:00Z")["view"]["attention"]
    assert attention == {"count": 1, "mailbox_known": False, "items": [
        {"source": "decision", "id": "d1", "request": "r0001", "task": "r0001.build", "kind": "question", "at": T0}]}


def test_attention_does_not_repair_or_truncate_mailbox(run):
    from alfrd.mailbox import USER
    from test_agent_mailbox import AGENT, answer, question
    root, _, _ = run
    box, first, _, _ = attention_mailbox(run)
    box.reply(first["id"], USER, answer())
    path = box.dir / "events.jsonl"
    path.write_text("")  # reply is durable; its resolution event was lost
    box.send(AGENT, question("expired", expires_at="2000-01-01T00:00:00Z"))
    path.write_text("")  # remove repairs/expiry written by send
    with (box.dir / "messages.jsonl").open("ab") as stream:
        stream.write(b'{"torn":')
    before = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    attention = harness.state(root, SID, scope="all")["view"]["attention"]
    assert attention["count"] == 2  # permission + other request; repaired/expired in memory
    assert before == {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}


# Frozen key/type contract from a010e7f using the synthetic replay fixture.
V1_SHAPE = {'schema': 'str',
 'project': 'str',
 'session': 'str',
 'plan': 'str',
 'scope': {'kind': 'str', 'request': 'NoneType'},
 'clock': {'mode': 'str', 'at': 'str', 'server_now': 'str'},
 'revision': {'session': 'int', 'last_seq': 'int', 'consistent': 'bool'},
 'coverage': {'complete': 'bool',
              'first_seq': 'int',
              'last_seq': 'int',
              'from': 'str',
              'to': 'str',
              'timezone': 'str',
              'warnings': ['str']},
 'settings': {'default_project': 'NoneType',
              'stuck_after_minutes': 'int',
              'poll_seconds': 'int',
              'role_order': ['str'],
              'default_scope': 'str',
              'feed_limit': 'int',
              'replay_speed': 'float',
              'show_completed': 'bool',
              'runner_stale_after_seconds': 'int',
              'refresh_seconds': 'int',
              'stuck_after_seconds': 'int'},
 'agents': [{'id': 'str',
             'label': 'str',
             'status': 'str',
             'tasks': ['str'],
             'units': [],
             'last_activity_at': 'NoneType',
             'stuck': {'suspected': 'bool', 'reason': 'NoneType', 'since': 'NoneType'},
             'usage': {'input_tokens': 'NoneType',
                       'output_tokens': 'NoneType',
                       'total_tokens': 'NoneType',
                       'total_cost_usd': 'NoneType',
                       'known_at': 'NoneType',
                       'complete': 'bool'},
             'model': 'str',
             'tasks_done': 'int'}],
 'handoff_edges': [{'id': 'str',
                    'request': 'str',
                    'from_task': 'str',
                    'to_task': 'str',
                    'from_agent': 'str',
                    'to_agent': 'str',
                    'kind': 'str',
                    'status': 'str',
                    'at': 'str',
                    'unit': 'NoneType',
                    'artifacts': [],
                    'source': 'str',
                    'historical_certainty': 'str'}],
 'timeline_segments': [{'id': 'str',
                        'request': 'str',
                        'task': 'str',
                        'unit': 'str',
                        'agent': 'str',
                        'attempt': 'int',
                        'state': 'str',
                        'start': 'str',
                        'end': 'str',
                        'open': 'bool',
                        'source_seq': 'int'}],
 'feed_events': [{'id': 'str',
                  'seq': 'int',
                  'at': 'str',
                  'kind': 'str',
                  'request': 'str',
                  'task': 'str',
                  'unit': 'str',
                  'agent': 'str',
                  'severity': 'str',
                  'text': 'str',
                  'data': {}}],
 'feed_page': {'has_more': 'bool', 'next_before_seq': 'NoneType'},
 'summary': {'requests': 'int',
             'tasks': {'total': 'int',
                       'pending': 'int',
                       'running': 'int',
                       'blocked': 'int',
                       'done': 'int',
                       'failed': 'int',
                       'cancelled': 'int',
                       'stopped': 'int'},
             'agents_active': 'int',
             'attempts': 'int',
             'stuck': 'int',
             'wall_seconds': 'float',
             'agent_seconds': 'float',
             'usage': {'input_tokens': 'NoneType',
                       'output_tokens': 'NoneType',
                       'total_tokens': 'NoneType',
                       'total_cost_usd': 'NoneType',
                       'known_at': 'NoneType',
                       'complete': 'bool'},
             'runner': {'alive': 'NoneType', 'heartbeat_at': 'NoneType', 'stale': 'NoneType'},
             'morning': {'finished': 'bool',
                         'handoffs': 'int',
                         'dependency_edges': 'int',
                         'longest_stuck_agent': 'NoneType',
                         'longest_stuck_seconds': 'NoneType',
                         'errors': [],
                         'note': 'str'}},
 'view': {'session': {'id': 'str', 'title': 'str', 'created': 'str'},
          'requests': [{'id': 'str', 'created': 'str', 'status': 'str'}],
          'tasks': [{'row': 'str',
                     'request': 'str',
                     'kind': 'str',
                     'owner': 'str',
                     'status': 'str',
                     'created': 'str',
                     'depends_on': ['str'],
                     'units': ['str'],
                     'task': {'title': 'str'}}],
          'graph': {'nodes': [{'id': 'str',
                               'request': 'str',
                               'kind': 'str',
                               'title': 'str',
                               'owner': 'str',
                               'status': 'str',
                               'status_text': 'str',
                               'depth': 'int'}],
                    'edges': [{'from': 'str', 'to': 'str', 'kind': 'str'}],
                    'groups': []},
          'messages': [],
          'decisions': [{'id': 'str',
                         'request': 'str',
                         'task': 'str',
                         'kind': 'str',
                         'needs': 'str',
                         'status': 'str',
                         'created': 'str'}],
          'blockers': [{'id': 'str',
                        'request': 'str',
                        'task': 'str',
                        'kind': 'str',
                        'needs': 'str',
                        'status': 'str',
                        'created': 'str'}],
          'usage': {'input_tokens': 'NoneType',
                    'output_tokens': 'NoneType',
                    'total_tokens': 'NoneType',
                    'total_cost_usd': 'NoneType',
                    'known_at': 'NoneType',
                    'complete': 'bool'},
          'settings': {'agents': {'claude': {'label': 'str', 'model': 'str'},
                                  'sonnet': {'label': 'str', 'model': 'str'},
                                  'codex': {'label': 'str'},
                                  'agy': {'label': 'str'}}},
          'read_only': 'bool'}}


def test_v1_keys_unchanged(run):
    root, store, _ = run
    decision_record(store, status="pending")
    state = harness.state(root, SID, scope="all", at="2026-11-01T12:01:00Z")

    def check(expected, actual):
        if isinstance(expected, dict):
            assert isinstance(actual, dict)
            assert expected.keys() <= actual.keys()
            for key, value in expected.items():
                check(value, actual[key])
        elif isinstance(expected, list):
            assert isinstance(actual, list)
            if expected:
                assert actual
                check(expected[0], actual[0])
        else:
            assert type(actual).__name__ == expected

    check(V1_SHAPE, state)
