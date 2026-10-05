"""Start a plan, or one of its steps, at a clock time ("02:00" / "2026-10-07 02:00")."""

from datetime import datetime, timedelta

import pytest

from test_agent_loop import project, service, task_client, wait  # noqa: F401
from alfrd.execution import ExecutionError, clock_time, is_clock, is_delay, next_clock
from alfrd.runtime import scheduler


def later(minutes=30):
    return (datetime.now() + timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")


def test_clock_times():
    assert is_clock("02:00") and is_clock("2026-10-07 02:00") and is_clock(datetime(2026, 10, 7, 2))
    assert is_delay("02:00") and not is_clock("+1h") and not is_clock("avica_avg")
    assert clock_time("2:05") == "02:05"
    assert next_clock("02:00", datetime(2026, 10, 6, 3)) == datetime(2026, 10, 7, 2)   # already past: tomorrow
    assert next_clock("02:00", datetime(2026, 10, 6, 1)) == datetime(2026, 10, 6, 2)
    for bad in ("25:00", "12:61", "2026-02-30 01:00", (datetime.now() + timedelta(days=8)).strftime("%Y-%m-%d 01:00")):
        with pytest.raises(ValueError):
            clock_time(bad)
    with pytest.raises(ExecutionError, match="start at"):
        scheduler.start_time("tomorrow")


def test_a_scheduled_plan_waits_and_start_now_runs_it(project):
    folder = scheduler.create_plan(project, start_at=later())
    assert folder.load()["start_at"]
    scheduler.spawn_runner(folder)
    try:
        assert wait(lambda: any(w.get("kind") == "start" for w in folder.load().get("waiting") or []))
        assert folder.units() == [] and folder.load()["status"] == "running"
        assert scheduler.reconcile(project, spawn=False) == []          # its runner is alive
        scheduler.start_now(project, folder.id)
        assert wait(lambda: folder.load()["status"] == "finished", timeout=60)
        plan = folder.load()
        assert plan["start_at"] is None and plan["started_at"] and len(folder.units()) == 10
    finally:
        if folder.load()["status"] == "running":
            scheduler.control(project, folder.id, "cancel")


def test_a_step_at_a_clock_time_waits_for_it(project):
    folder = scheduler.create_plan(project)
    when = later()
    scheduler.set_override(project, folder.id, "t002-codex", {"after": when})
    scheduler.spawn_runner(folder)
    try:
        assert wait(lambda: any(w.get("kind") == "delay" for w in folder.load().get("waiting") or []))
        delayed = next(w for w in folder.load()["waiting"] if w.get("kind") == "delay")
        assert delayed["step"] == "t002-codex" and delayed["until"] == when.replace(" ", "T")
        assert len(folder.units()) == 1
        scheduler.set_override(project, folder.id, "t002-codex", {"after": 0})   # Run now
        assert wait(lambda: folder.load()["status"] == "finished", timeout=60)
    finally:
        if folder.load()["status"] == "running":
            scheduler.control(project, folder.id, "cancel")


def test_the_first_step_can_wait_for_a_clock_time(project):
    folder = scheduler.create_plan(project)
    scheduler.set_override(project, folder.id, "t001-claude", {"after": later()})
    scheduler.spawn_runner(folder)
    try:
        assert wait(lambda: any(w.get("step") == "t001-claude" for w in folder.load().get("waiting") or []))
        assert folder.units() == []
    finally:
        scheduler.control(project, folder.id, "cancel")


def test_a_scheduled_plan_whose_runner_died_is_restarted(project, monkeypatch):
    folder = scheduler.create_plan(project, start_at=later())
    monkeypatch.setattr(scheduler.PlanDir, "runner_alive", lambda self: False)
    monkeypatch.setattr(scheduler, "_starting", lambda plan: False)
    assert scheduler.reconcile(project, spawn=False) == [{"plan": folder.id, "action": "needs runner", "alive": []}]


def test_studio_api_schedules_and_starts_now(service, tmp_path, project):
    client, headers, prefix = task_client(service, tmp_path)
    response = client.post(prefix + "/plans", json={"start_at": later(), "start": False}, headers=headers)
    assert response.status_code == 201, response.get_json()
    plan = response.get_json()["plan"]
    assert plan["start_at"]
    started = client.post(prefix + f"/plans/{plan['id']}/start-now", json={}, headers=headers)
    assert started.status_code == 200 and started.get_json()["plan"]["start_at"] is None
    assert client.post(prefix + "/plans", json={"start_at": "noon", "start": False}, headers=headers).status_code == 400
    turns = client.get(prefix + f"/plans/{plan['id']}/turns").get_json()["turns"]
    assert "at" in turns[0]


def test_a_row_waiting_for_its_time_does_not_take_the_only_slot(tmp_path):
    """concurrency 1: while alpha's next step waits for its time, beta runs (it used to sit idle)."""
    from alfrd.runtime import plan_csv as pc

    steps = ["a", "b"]
    path = tmp_path / "plan.csv"
    pc.create(path, [{"target": "alpha"}, {"target": "beta"}], steps, steps,
              key_column="TARGET_NAME", files_column="FILENAMES", code_column="PROJECT_CODE", workdir_column="WORKDIR")
    table = pc.read(path, steps, key_column="TARGET_NAME", files_column="FILENAMES", code_column="PROJECT_CODE",
                    workdir_column="WORKDIR")
    wait_entry = {"row": "alpha", "kind": "delay", "reason": "waiting: b starts after …"}
    picked = scheduler.pick_rows(table, mode="step", on_failure="stop_target", limit=1, free=1, names=[],
                                 hold=lambda row, spec: wait_entry if row.key == "alpha" else None)
    assert [row.key for row, _spec in picked.start] == ["beta"]
    assert picked.waiting == [wait_entry]
