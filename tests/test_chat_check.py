"""Scheduled checks of a chat session (``chat.watch``, ``chat.check``): code recovery and optional bounded orchestrator calls."""
# ruff: noqa: F811 - `project` is the imported fixture, taken by name
from __future__ import annotations

import json
import os
import time

import pytest
from test_chat_orchestration import (
    calls,
    configure,
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


def check_with_live_runner(project, sid, monkeypatch, **kwargs):
    """A live runner which has stopped making progress, without launching a real daemon."""
    store = chat.session(project, sid)
    with monkeypatch.context() as patch:
        patch.setattr(type(chat._plan_folder(store)), "runner_alive", lambda self: True)
        return chat.check(project, sid, spawn=False, **kwargs)[0]


def test_stall_with_wake_off_never_adds_an_agent_call(project, monkeypatch):
    fake(project, plan=plan(task("a")), a=result())
    sid = chat.create_session(project)["id"]
    say(project, sid, "do a")
    report = check_with_live_runner(project, sid, monkeypatch, now=time.time() + 1801)
    assert report["findings"] == []
    assert calls(project) == []
    assert not any(t["kind"] == "check" for t in chat.session(project, sid).tasks())


def test_stall_check_plan_reply_adds_tasks_and_message(project, monkeypatch):
    configure(project, watch={"wake_orchestrator": True})
    fake(project, plan=plan(task("a")), a=result(), b=result(),
         check=plan(task("b", deps=["a"]), message="Recovery planned"))
    sid = chat.create_session(project)["id"]
    say(project, sid, "do a")
    report = check_with_live_runner(project, sid, monkeypatch, now=time.time() + 1801)
    assert [f["kind"] for f in report["findings"]] == ["orchestrator_woken"]
    # Repeated checks cannot duplicate a queued check, even after another stall period.
    check_with_live_runner(project, sid, monkeypatch, now=time.time() + 3602)
    store = chat.session(project, sid)
    assert len([t for t in store.tasks() if t["kind"] == "check"]) == 1
    state = run(project, sid)
    assert state["requests"][0]["status"] == "done"
    assert store.task("r0001.b")["status"] == "done"
    assert store.request("r0001")["checks"] == 1
    assert [c["kind"] for c in calls(project)].count("check") == 1
    prompt = next(c["prompt"] for c in calls(project) if c["kind"] == "check")
    assert "idle for" in prompt and "Findings:" in prompt and "r0001.plan" in prompt
    assert any(m["text"] == "Recovery planned" for m in store.messages())
    assert any(m.get("kind") == "orchestrator_woken" for m in store.messages())


@pytest.mark.parametrize("limit", [0, 1, 2])
def test_stall_check_limit_leaves_the_request_for_the_user(project, monkeypatch, limit):
    configure(project, watch={"wake_orchestrator": True, "max_checks": limit})
    fake(project, plan=plan(task("a")), a=result(), check=plan(message="Please review"))
    sid = chat.create_session(project)["id"]
    say(project, sid, "do a")
    for _ in range(limit):
        check_with_live_runner(project, sid, monkeypatch, now=time.time() + 1801)
        run(project, sid)
        store = chat.session(project, sid)
        req = store.request("r0001")
        req["status"] = "running"
        store.save_request(req)
        rec = store.task("r0001.a")
        rec["status"] = "queued"  # work remains stuck after the check
        store.save_task(rec)
    report = check_with_live_runner(project, sid, monkeypatch, now=time.time() + 1801)
    assert [f["kind"] for f in report["findings"]] == ["check_limit_reached"]
    assert len([t for t in chat.session(project, sid).tasks() if t["kind"] == "check"]) == limit
    assert [c["kind"] for c in calls(project)].count("check") == limit


def test_wake_enabled_without_a_stall_adds_no_check(project, monkeypatch):
    configure(project, watch={"wake_orchestrator": True})
    fake(project, plan=plan(task("a")), a=result())
    sid = chat.create_session(project)["id"]
    say(project, sid, "do a")
    assert check_with_live_runner(project, sid, monkeypatch)["findings"] == []
    assert calls(project) == []
    # A dead runner gets code recovery first, even if the request is old.
    report = chat.check(project, sid, spawn=False, now=time.time() + 1801)[0]
    assert [f["kind"] for f in report["findings"]] == ["runner_resumed"]
    report = chat.check(project, sid, spawn=False, now=time.time() + 1802)[0]
    assert [f["kind"] for f in report["findings"]] == ["runner_resumed", "orchestrator_woken"]


def test_orphaned_unit_wakes_the_orchestrator(project, monkeypatch):
    configure(project, watch={"wake_orchestrator": True})
    fake(project, plan=plan(task("a")))
    sid = chat.create_session(project)["id"]
    say(project, sid, "do a")
    store = chat.session(project, sid)
    folder = chat._plan_folder(store)
    folder.units_dir.mkdir(parents=True, exist_ok=True)
    unit = {"id": "orphan", "row": "r0001.plan", "status": "running", "pid": 99999999}
    (folder.units_dir / "orphan.json").write_text(json.dumps(unit))
    report = check_with_live_runner(project, sid, monkeypatch, now=time.time() + 1801)
    assert [f["kind"] for f in report["findings"]] == ["unit_orphaned", "orchestrator_woken"]
    prompt = (store.tasks_dir / "r0001.check-1" / "prompt.md").read_text()
    assert "unit_orphaned" in prompt and "orphan" in prompt


def test_wake_enabled_preserves_pause_even_on_a_stall(project):
    configure(project, watch={"wake_orchestrator": True})
    sid = chat.create_session(project)["id"]
    say(project, sid, "do a")
    scheduler.control(project, f"chat-{sid}", "pause", spawn=False)
    report = chat.check(project, sid, spawn=False, now=time.time() + 1801)[0]
    assert [f["kind"] for f in report["findings"]] == ["runner_held"]
    assert chat._plan_folder(chat.session(project, sid)).control() == "pause"
    assert calls(project) == []


def test_a_task_still_writing_its_log_is_not_a_stall(project, monkeypatch, tmp_path):
    configure(project, watch={"wake_orchestrator": True})
    sid = chat.create_session(project)["id"]
    say(project, sid, "do a")
    now = time.time() + 1801
    log = tmp_path / "plan.log"
    log.write_text("working\n")
    os.utime(log, (now, now))  # the agent wrote output just now; its unit record is old
    folder = chat._plan_folder(chat.session(project, sid))
    folder.units_dir.mkdir(parents=True, exist_ok=True)
    unit = {"id": "busy", "row": "r0001.plan", "status": "running", "pid": os.getpid(), "log": str(log)}
    (folder.units_dir / "busy.json").write_text(json.dumps(unit))
    monkeypatch.setattr(scheduler, "pid_alive", lambda *a, **k: True)
    assert check_with_live_runner(project, sid, monkeypatch, now=now)["findings"] == []
    assert not any(t["kind"] == "check" for t in chat.session(project, sid).tasks())
    # Once the log goes quiet for the stall period, it is a stall.
    report = check_with_live_runner(project, sid, monkeypatch, now=now + 1801)
    assert [f["kind"] for f in report["findings"]] == ["orchestrator_woken"]


def test_a_stall_check_cannot_answer_a_decision_that_went_to_the_user(project):
    configure(project, watch={"wake_orchestrator": True}, delegate_decisions="questions", bounds={"max_replans": 0})
    question = result("blocked", "unsure", blockers=[{"id": "api", "reason": "v1 or v2?", "options": ["v1", "v2"]}])
    fake(project, plan=plan(task("a")), a=question)
    sid = chat.create_session(project)["id"]
    say(project, sid, "build the api")
    run(project, sid)
    store = chat.session(project, sid)
    d = store.decision("r0001.a.api")
    d["needs"] = "orchestrator"  # its replan was lost: the stall check may answer it ...
    store.save_decision(d)
    with store.lock():
        assert chat._wake_for_stall(store, chat.settings(project), store.request("r0001"), [], 1801)
    assert store.task("r0001.check-1")["blockers"] == ["r0001.a.api"]
    d["needs"] = "user"  # ... until it is handed back to the user
    store.save_decision(d)
    fake(project, plan=plan(task("a")), a=question, check=plan(decisions=[{"id": "r0001.a.api", "choice": "v2"}]))
    run(project, sid)
    d = store.decision("r0001.a.api")
    assert d["status"] == "pending" and d["needs"] == "user" and "decided_by" not in d
