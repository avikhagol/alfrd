"""Structured plan events (alfrd.events, Runner.event, events.jsonl)."""
import json
import os
import re
import sys
from pathlib import Path

import pytest
import yaml

from test_agent_loop import project, service, wait  # noqa: F401
from test_loop_features import set_turns
from alfrd.agent_loop import approve_response, reject_response, sha256
from alfrd.api import status as api
from alfrd.events import EVENT_KINDS, EVENTS_FILE, EventLog, kind_matches
from alfrd.runtime import scheduler

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")


def events(folder):
    return [json.loads(line) for line in (folder.path / EVENTS_FILE).read_text().splitlines()]


# -- EventLog ----------------------------------------------------------------

def test_kinds_and_prefix_matching():
    assert len(EVENT_KINDS) == 17
    assert kind_matches("plan.*", "plan.failed") and kind_matches("review.pending", "review.pending")
    assert kind_matches("*", "turn.idle")
    assert not kind_matches("plan.*", "planner.x") and not kind_matches("plan", "plan.failed")
    assert not kind_matches("turn.started", "turn.finished")


def test_seq_is_monotonic_and_survives_a_torn_last_line(tmp_path):
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    assert [log.append({"kind": "turn.started"}) for _ in range(3)] == [1, 2, 3]
    with open(path, "ab") as fh:
        fh.write(b'{"seq": 4, "kind": "tur')  # the writer died mid-line
    again = EventLog(path)  # a new runner on the same plan
    assert again.last_seq() == 3 and again.append({"kind": "turn.finished"}) == 4
    lines = path.read_text().splitlines()
    assert json.loads(lines[-1])["seq"] == 4 and lines[-2].startswith('{"seq": 4, "kind": "tur')
    assert [e["seq"] for e in again.read_since(0)] == [1, 2, 3, 4]
    assert [e["seq"] for e in again.read_since(2, limit=1)] == [3]


def test_rotation_keeps_one_file_and_continues_seq(tmp_path):
    path = tmp_path / "events.jsonl"
    log = EventLog(path, max_bytes=400)
    for _ in range(20):
        log.append({"kind": "turn.started", "text": "x" * 40})
    rotated = path.with_name("events.jsonl.1")
    assert rotated.exists() and not path.with_name("events.jsonl.2").exists()
    assert path.stat().st_size <= 400 and rotated.stat().st_size <= 400
    first = json.loads(path.read_text().splitlines()[0])["seq"]
    assert json.loads(rotated.read_text().splitlines()[-1])["seq"] == first - 1
    seqs = [e["seq"] for e in log.read_since(0)]
    assert seqs == list(range(seqs[0], 21))
    path.unlink()  # as if it had just rotated: the count comes from .1
    assert EventLog(path, max_bytes=400).append({"kind": "turn.started"}) == first


# -- Runner.event --------------------------------------------------------------

def test_runner_event_fields_unknown_kind_and_write_failure(project, monkeypatch):
    folder = scheduler.create_plan(project)
    runner = scheduler.Runner(project, folder.id)
    history = list(folder.load()["history"])
    runner.event("turn.idle", history=False, unit={"id": "u1", "target": "task", "iteration": 2, "agent": "codex",
                                                   "handoff": {"roles": ["Reviewer"]}}, quiet_for=5)
    [line] = events(folder)
    assert line == {**line, "seq": 1, "kind": "turn.idle", "plan": folder.id, "project": str(project.resolve()),
                    "target": "task", "unit": "u1", "turn": 2, "turns": 10, "agent": "codex", "roles": ["Reviewer"],
                    "text": None, "data": {"quiet_for": 5}}
    assert folder.load()["history"] == history, "history=False adds no history line"
    with pytest.raises(ValueError, match="unknown event kind"):
        runner.event("plan.exploded", "boom")
    # A failing write is logged, never raised; history still gets its text.
    monkeypatch.setattr(EventLog, "append", lambda self, record: (_ for _ in ()).throw(OSError("disk full")))
    runner.event("plan.started", "hello")
    assert folder.load()["history"][-1]["event"] == "hello"
    # Re-adoption: a new Runner continues the count.
    monkeypatch.undo()
    scheduler.Runner(project, folder.id).event("plan.started", "again")
    assert [e["seq"] for e in events(folder)] == [1, 2]


def test_loop_run_writes_valid_events_and_unchanged_history(project):
    set_turns(project, 2)
    folder = scheduler.create_plan(project)
    scheduler.Runner(project, folder.id).run()
    lines = events(folder)
    assert all(e["kind"] in EVENT_KINDS for e in lines)
    assert [e["seq"] for e in lines] == list(range(1, len(lines) + 1))
    assert [e["kind"] for e in lines] == [
        "plan.started",
        "turn.started", "handoff.published", "turn.finished",
        "turn.started", "handoff.published", "turn.finished",
        "plan.finished"]
    turns = [e for e in lines if e["kind"].startswith(("turn.", "handoff."))]
    assert [e["turn"] for e in turns] == [1, 1, 1, 2, 2, 2] and all(e["turns"] == 2 for e in turns)
    assert [e["agent"] for e in turns][::3] == ["claude", "codex"] and all(e["unit"] for e in turns)
    assert lines[-1]["data"]["status"] == "finished"
    # history: exactly the texts it had before structured events.
    texts = [h["event"] for h in folder.load()["history"]]
    assert texts[0] == "created" and re.fullmatch(rf"runner {os.getpid()} on .+ started", texts[1])
    units = [u["id"] for u in folder.units()]
    assert texts[2:4] == [f"handoff published: {units[0]} → task/next-step-codex.md",
                          f"handoff published: {units[1]} → task/next-step-claude.md"]
    assert texts[4].startswith("runner stopped: finished (") and len(texts) == 5


def test_failed_turn(project, monkeypatch):
    monkeypatch.setenv("FAIL_AGENT", "1")
    folder = scheduler.create_plan(project)
    scheduler.Runner(project, folder.id).run()
    kinds = [e["kind"] for e in events(folder)]
    # on_failure: stop_plan adds its own turn.failed (D4), with stop_plan set and no unit.
    assert kinds == ["plan.started", "turn.started", "turn.failed", "turn.failed", "plan.failed"]
    failed, stop = events(folder)[2:4]
    assert failed["data"]["exit_code"] == 7 and failed["unit"]
    assert stop["data"]["stop_plan"] is True and stop["unit"] is None


def test_runtime_limit_reached_once(project):
    data = yaml.safe_load((project / "alfrd.yaml").read_text())
    data["execution"]["max_runtime"] = 1
    data["execution"]["kill_grace"] = .2
    (project / "alfrd.yaml").write_text(yaml.safe_dump(data))
    (project / "hold").touch()
    try:
        folder = scheduler.create_plan(project)
        scheduler.Runner(project, folder.id).run()
    finally:
        (project / "hold").unlink(missing_ok=True)
    limits = [e for e in events(folder) if e["kind"] == "limit.reached"]
    assert len(limits) == 1 and limits[0]["data"] == {"limit": "max_runtime", "seconds": 1}
    assert events(folder)[-1]["kind"] == "plan.failed"
    assert "total runtime limit reached" not in [h["event"] for h in folder.load()["history"]]


def test_review_pending_and_approved(project):
    set_turns(project, 2, turns={1: {"human_review": True}})
    folder = scheduler.create_plan(project)
    scheduler.spawn_runner(folder)
    try:
        assert wait(lambda: folder.units() and folder.units()[0].get("review_status") == "pending")
        unit = folder.units()[0]
        text = Path(unit["handoff"]["response_file"]).read_text()
        approve_response(project, folder.id, unit["id"], text, sha256(text))
        assert wait(lambda: folder.load()["status"] == "finished")
    finally:
        if folder.load()["status"] == "running":
            scheduler.control(project, folder.id, "cancel")
    kinds = [e["kind"] for e in events(folder)]
    assert kinds.index("review.pending") < kinds.index("review.approved") < kinds.index("handoff.published")
    assert kinds.count("review.pending") == 1 and kinds.count("review.approved") == 1
    texts = [h["event"] for h in folder.load()["history"]]
    assert f"awaiting human review: {unit['id']}" in texts


def test_review_rejected(project):
    set_turns(project, 2, turns={1: {"human_review": True}})
    second = scheduler.create_plan(project)
    scheduler.spawn_runner(second)
    try:
        assert wait(lambda: second.units() and second.units()[0].get("review_status") == "pending")
        reject_response(project, second.id, second.units()[0]["id"], "wrong direction")
        assert wait(lambda: second.load()["status"] == "failed")
    finally:
        if second.load()["status"] == "running":
            scheduler.control(project, second.id, "cancel")
    rejected = [e for e in events(second) if e["kind"] == "review.rejected"]
    assert len(rejected) == 1 and rejected[0]["data"]["reason"] == "wrong direction"
    assert not any(h["event"].startswith("review rejected") for h in second.load()["history"])


# -- API -----------------------------------------------------------------------

def test_structured_events_api(project):
    set_turns(project, 1)
    folder = scheduler.create_plan(project)
    scheduler.Runner(project, folder.id).run()
    doc = api.structured_events(project, folder.id, since=0)
    assert doc["schema"] == "alfrd.plan_events/1" and doc["plan"] == folder.id
    assert doc["seq"] == len(doc["events"]) == 5
    assert api.structured_events(project, None, since=2, limit=1)["events"][0]["seq"] == 3
    assert api.structured_events(project, folder.id, since=5) == {**doc, "events": [], "seq": 5}
    with pytest.raises(api.StatusNotFound):
        api.structured_events(project, "nope", since=0)
