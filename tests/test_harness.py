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
