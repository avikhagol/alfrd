"""Which run the Studio shows when none is chosen (the hidden active run fix, D7)."""
import json
import os

from alfrd.runtime import scheduler
from alfrd.runtime.scheduler import PlanDir, plan_status, proc_start


def add_plan(root, pid, created, status="running", units=(), **extra):
    folder = PlanDir(root, pid)
    folder.path.mkdir(parents=True)
    plan = {"id": pid, "created": created, "status": status, "csv": "plan.csv", "target": f"t-{pid}",
            "loop": {"iterations": 4}, **extra}
    folder.plan_file.write_text(json.dumps(plan))
    for i, unit in enumerate(units):
        folder.save_unit({"id": f"{i:04d}-{pid}", "agent": "claude", "iteration": i + 1, **unit})
    return folder


def alive():
    return {"status": "running", "pid": os.getpid(), "proc_start": proc_start(os.getpid())}


def dead():
    return {"status": "running", "pid": 999999999}


def test_a_newer_scheduled_run_does_not_hide_the_working_one(tmp_path, monkeypatch):
    # The test process stands in for the agent; under an alfrd run it inherits another ALFRD_ROOT.
    monkeypatch.setattr(scheduler, "owned_by", lambda pid, root: True)
    add_plan(tmp_path, "old", "2026-10-07T10:00:00", units=[{"status": "ok"}, alive()])
    add_plan(tmp_path, "new", "2026-10-07T22:00:00", start_at="2026-10-07T22:45:00")
    assert scheduler.default_plan_id(tmp_path) == "old"
    doc = plan_status(tmp_path)
    assert doc["plan"]["id"] == "old" and doc["selected"] == "default"
    listed = {p["id"]: p for p in doc["plans"]}
    assert listed["old"].items() >= {"working": True, "phase": "running", "turn": 2, "turns": 4, "agent": "claude"}.items()
    assert listed["new"]["working"] is False and listed["new"]["phase"] == "scheduled"
    assert listed["new"]["start_at"] == "2026-10-07T22:45:00"
    picked = plan_status(tmp_path, "new")
    assert picked["plan"]["id"] == "new" and picked["selected"] == "id"


def test_review_and_manual_turns_count_as_working(tmp_path):
    add_plan(tmp_path, "a", "2026-10-07T09:00:00", units=[{"status": "ok", "review_status": "pending"}])
    add_plan(tmp_path, "b", "2026-10-07T10:00:00", units=[dead()])
    assert scheduler.default_plan_id(tmp_path) == "a"
    assert scheduler.plan_activity(tmp_path, scheduler.PlanDir(tmp_path, "a").load())["phase"] == "review"
    add_plan(tmp_path, "c", "2026-10-07T11:00:00", units=[{"status": "running", "manual": True}])
    assert scheduler.default_plan_id(tmp_path) == "c"
    assert scheduler.plan_activity(tmp_path, scheduler.PlanDir(tmp_path, "c").load())["phase"] == "manual"


def test_fallbacks(tmp_path):
    assert scheduler.default_plan_id(tmp_path) is None and plan_status(tmp_path)["plan"] is None
    add_plan(tmp_path, "done", "2026-10-07T08:00:00", status="finished")
    add_plan(tmp_path, "newest", "2026-10-07T12:00:00", status="cancelled")
    assert scheduler.default_plan_id(tmp_path) == "newest"  # nothing active: the newest run
    add_plan(tmp_path, "sched", "2026-10-07T11:00:00", start_at="2026-10-08T02:00:00")
    assert scheduler.default_plan_id(tmp_path) == "sched"  # only a scheduled run is active
    add_plan(tmp_path, "idle", "2026-10-07T10:00:00", units=[dead()])
    assert scheduler.default_plan_id(tmp_path) == "idle"  # active and not waiting for a start time
    add_plan(tmp_path, "paused", "2026-10-07T10:30:00", status="paused")
    assert scheduler.default_plan_id(tmp_path) == "paused"
    listed = {p["id"]: p for p in plan_status(tmp_path)["plans"]}
    assert listed["paused"]["phase"] == "paused" and listed["idle"]["phase"] == "waiting"
    assert listed["done"].items() >= {"working": False, "phase": "finished", "turn": None, "agent": None}.items()


def test_active_runs_beyond_the_newest_twenty_are_listed(tmp_path):
    add_plan(tmp_path, "busy", "2026-10-01T00:00:00", units=[alive()])
    for i in range(25):
        add_plan(tmp_path, f"f{i:02d}", f"2026-10-07T{i % 24:02d}:{i:02d}:00", status="finished")
    doc = plan_status(tmp_path)
    assert doc["plan"]["id"] == "busy" and len(doc["plans"]) == 21 and doc["plans"][-1]["id"] == "busy"
