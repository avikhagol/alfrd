"""Scheduled checks of a chat session (``chat.watch``, ``chat.check``): code only, no LLM call."""
# ruff: noqa: F811 - `project` is the imported fixture, taken by name
from __future__ import annotations

import json
import time

import pytest
from test_chat_orchestration import (
    calls,
    events,
    fake,
    plan,
    project,  # noqa: F401 - the fixture (tests below take it by name)
    result,
    run,
    say,
    task,
)

from alfrd import chat
from alfrd.execution import ExecutionError
from alfrd.runtime import scheduler


def test_watch_settings_merge_per_key_and_are_validated():
    assert chat.merge_settings({})["watch"] == {"interval_seconds": 300, "stall_seconds": 1800,
                                                "wake_orchestrator": False, "max_checks": 3}
    assert chat.merge_settings({"watch": {"interval_seconds": 0}})["watch"]["stall_seconds"] == 1800
    for bad in ({"interval_seconds": 10}, {"interval_seconds": -1}, {"stall_seconds": 5}, {"max_checks": True},
                {"wake_orchestrator": "yes"}, {"every": 60}):
        with pytest.raises(ExecutionError, match="chat.watch"):
            chat.merge_settings({"watch": bad})


def test_check_resumes_a_dead_runner_that_has_work_left(project, monkeypatch):
    fake(project, plan=plan(task("a")), a=result())
    sid = chat.create_session(project)["id"]
    say(project, sid, "do a")                             # rows queued, no runner (as after a crash or reboot)
    spawned = []
    monkeypatch.setattr(scheduler, "spawn_runner", lambda folder, launcher=None: spawned.append(folder) or 1)
    [report] = chat.check(project, sid)
    assert [f["kind"] for f in report["findings"]] == ["runner_resumed"] and len(spawned) == 1
    assert not report["runner_alive"] and report["next_at"]
    saved = json.loads((project / ".alfrd" / "chat" / sid / "check.json").read_text())
    assert saved["findings"][0]["kind"] == "runner_resumed"
    assert any(e["kind"] == "chat.check" for e in events(project, sid))
    assert calls(project) == []                           # the check itself calls no agent
    state = run(project, sid)
    assert state["requests"][0]["status"] == "done"


def test_check_leaves_a_paused_session_alone(project, monkeypatch):
    fake(project, plan=plan(task("a")), a=result())
    sid = chat.create_session(project)["id"]
    say(project, sid, "do a")
    scheduler.control(project, f"chat-{sid}", "pause", spawn=False)
    monkeypatch.setattr(scheduler, "spawn_runner", lambda *a, **k: pytest.fail("a paused session was resumed"))
    [report] = chat.check(project, sid)
    assert [f["kind"] for f in report["findings"]] == ["runner_held"]


def test_check_finishes_a_request_stalled_after_its_tasks_ended(project):
    fake(project, plan=plan(task("a")), a=result())
    sid = chat.create_session(project)["id"]
    say(project, sid, "do a")
    run(project, sid)
    store = chat.session(project, sid)
    req = store.request("r0001")
    assert req["status"] == "done"
    req["status"] = "running"                             # a crash between the last result and finishing it
    req.pop("finished", None)
    store.save_request(req)
    [early] = chat.check(project, sid, spawn=False)       # not idle for stall_seconds yet: left to the runner
    assert early["findings"] == [] and store.request("r0001")["status"] == "running"
    [report] = chat.check(project, sid, spawn=False, now=time.time() + 1801)
    assert [f["kind"] for f in report["findings"]] == ["request_finished"]
    assert store.request("r0001")["status"] == "done"
    assert [c["kind"] for c in calls(project)] == ["plan", "task"]


def test_check_of_every_session_skips_idle_ones(project):
    a = chat.create_session(project)["id"]
    b = chat.create_session(project)["id"]
    reports = chat.check(project)
    assert {r["session"] for r in reports} == {a, b} and all(r["findings"] == [] for r in reports)
    assert not any(e["kind"] == "chat.check" for e in events(project, a))
