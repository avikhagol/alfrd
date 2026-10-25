"""Authoritative steps: identity, monotonic replay and scheduler/chat integration."""
from dataclasses import asdict
import json

import pytest

from alfrd import chat
from alfrd.events import EventLog
from alfrd.steps import StepProjection, project_steps, replay_steps
from tests.test_chat_orchestration import (  # noqa: F401
    configure, events, fake, plan, project, result, run, say, task,
)


def event(kind, *, row="r1.a", unit=None, task_kind="task", **data):
    return {"kind": kind, "plan": "chat-s1", "at": "2026-10-25T00:00:00Z", "unit": unit,
            "target": row, "data": {"request": "r1", "task": row, "task_kind": task_kind, **data}}


def execution(projection):
    return [s for s in projection.steps if s.kind == "execute"]


def test_stable_identity_and_forward_only_transitions():
    p = StepProjection()
    p.apply(event("task.created"))
    queued = execution(p)[0]
    p.apply(event("task.started", unit="u1"))
    running = execution(p)[0]
    assert running.id == queued.id and running.status == "running"
    p.apply(event("task.blocked", unit="u1", waiting_on=["msg1", "msg1"]))
    waiting = execution(p)[0]
    assert waiting.id == queued.id and waiting.status == "waiting" and waiting.waiting_on == ["msg1"]
    p.apply(event("task.started", unit="u1"))  # re-adoption cannot roll waiting back
    assert execution(p)[0] == waiting
    p.apply(event("task.finished", unit="u1", status="cancelled"))
    finished = execution(p)[0]
    for stale in [event("task.created"), event("task.started", unit="u1"),
                  event("task.finished", unit="u1", status="done")]:
        p.apply(stale)
    assert execution(p)[0] == finished
    assert finished.finished_at and finished.status == "cancelled"
    assert queued.status == "queued"  # earlier snapshots remain unchanged
    other = StepProjection()
    other.apply(event("task.created", row="r1.unrelated"))
    other.apply(event("task.created"))
    assert execution(other)[1].id == queued.id
    other_plan = {**event("task.created"), "plan": "chat-s2"}
    assert project_steps([other_plan])[0].id != queued.id


@pytest.mark.parametrize("task_kind", ["plan", "repair", "replan", "summary"])
def test_orchestrator_calls_require_validated_results(task_kind):
    p = StepProjection()
    p.apply(event("task.created", task_kind=task_kind))
    p.apply(event("task.started", task_kind=task_kind, unit="call1"))
    p.apply(event("turn.finished", task_kind=task_kind, unit="call1", status="done"))
    assert p.steps[0].status == "running"
    p.apply(event("task.finished", task_kind=task_kind, unit="call1", status="failed"))
    assert len(p.steps) == 1 and p.steps[0].status == "failed"
    assert p.steps[0].kind == task_kind and p.steps[0].unit == "call1"


@pytest.mark.parametrize("scheduler_attempt", [1, 2])
def test_retry_gets_distinct_attempt_even_when_scheduler_counter_resets(scheduler_attempt):
    p = StepProjection()
    for e in [event("task.created"), event("task.started", unit="u1"),
              event("task.finished", unit="u1", status="retrying"),
              event("task.started", unit="u2", attempt=scheduler_attempt),
              event("task.started", unit="u2", attempt=scheduler_attempt),
              event("task.finished", unit="u1", status="failed"),
              event("task.finished", unit="u2", status="done")]:
        p.apply(e)
    first, second = execution(p)
    assert [first.attempt, second.attempt] == [1, 2]
    assert first.id != second.id and [first.status, second.status] == ["failed", "done"]
    assert [first.unit, second.unit] == ["u1", "u2"]


def test_successful_exit_with_provider_limit_is_failed_before_fallback_starts():
    p = StepProjection()
    for e in [event("task.created"), event("task.started", unit="u1"),
              event("turn.finished", unit="u1", status="done"),
              event("task.fallback", unit="u1")]:
        p.apply(e)
    assert execution(p)[0].status == "failed"
    p.apply(event("task.started", unit="fallback", attempt=1))
    assert [s.status for s in execution(p)] == ["failed", "running"]


def test_replay_includes_rotation_pages_and_ignores_torn_tail(tmp_path):
    path = tmp_path / "events.jsonl"
    log = EventLog(path, max_bytes=10**9)
    log.append(event("task.created"))
    path.rename(log.rotated)
    for n in range(510):
        log.append(event("budget.warning", value=n))
    log.append(event("task.started", unit="u1"))
    log.append(event("task.finished", unit="u1", status="done"))
    expected = project_steps(log.read_since(limit=1000))
    with path.open("a") as stream:
        stream.write('{"seq":514,"kind":')
    assert replay_steps(path) == expected == replay_steps(path)
    assert next(s for s in expected if s.kind == "execute").status == "done"


@pytest.mark.parametrize("code", ["PRG", "DON", "ERR", "STR", "REP"])
def test_mail_cannot_complete_or_fail_a_step(code):
    p = StepProjection()
    p.apply(event("task.created"))
    p.apply(event("task.started", unit="u1"))
    before = p.steps
    # Both mailbox records and envelopes whose payload claims completion are ignored.
    p.apply({"kind": "reply" if code == "REP" else "info", "code": code,
             "step_id": before[0].id, "state": "resolved", "status": "done",
             "payload": event("task.finished", unit="u1", status="done")})
    p.apply(event("mailbox.resolved", status="done"))
    assert p.steps == before


def test_failed_launch_without_started_event_and_queued_cancellation():
    failed = project_steps([event("task.created"), event("turn.failed", unit="u1", status="failed")])
    step = next(s for s in failed if s.kind == "execute")
    assert step.status == "failed" and step.unit == "u1" and step.started_at is None
    cancelled = project_steps([event("task.created"), event("task.finished", status="cancelled")])
    assert next(s for s in cancelled if s.kind == "execute").status == "cancelled"


def test_session_steps_replay_plan_delegate_execute_repair_summary(project):
    configure(project, final_summary=True)
    fake(project, plan={"text": "bad plan"}, repair=plan(task("a")), a=result(), summary={"text": "All done"})
    sid = chat.create_session(project)["id"]
    say(project, sid, "work")
    queued = chat.session_state(project, sid)["requests"][0]["steps"][0]
    state = run(project, sid)
    steps = state["requests"][0]["steps"]
    assert {s["kind"] for s in steps} == {"plan", "repair", "delegate", "execute", "summary"}
    assert next(s for s in steps if s["kind"] == "plan")["id"] == queued["id"]
    assert {s["status"] for s in steps if s["kind"] != "plan"} == {"done"}
    assert next(s for s in steps if s["kind"] == "plan")["status"] == "failed"
    path = project / ".alfrd" / "plans" / f"chat-{sid}" / "events.jsonl"
    assert steps == [asdict(s) for s in replay_steps(path)]
    assert steps == [asdict(s) for s in project_steps(events(project, sid))]


def test_real_scheduler_retry_preserves_first_failure(project):
    fake(project, t1=[{"exit": 2}, result()])
    sid = chat.create_session(project)["id"]
    say(project, sid, "@codex work")
    state = run(project, sid)
    steps = [s for s in state["requests"][0]["steps"] if s["kind"] == "execute"]
    assert [s["status"] for s in steps] == ["failed", "done"]
    assert [s["attempt"] for s in steps] == [1, 2]
    assert len({s["id"] for s in steps}) == 2


def test_replan_resume_retires_waiting_attempt(project):
    blocked = result("blocked", blockers=[{
        "id": "api", "reason": "v1 or v2?", "options": ["v1", "v2"], "needs": "orchestrator"}])
    fake(project, plan=plan(task("a")), a=[blocked, result()],
         replan=plan(decisions=[{"id": "r0001.a.api", "choice": "v2"}]))
    sid = chat.create_session(project)["id"]
    say(project, sid, "build the api")
    steps = run(project, sid)["requests"][0]["steps"]
    assert next(s for s in steps if s["kind"] == "replan")["status"] == "done"
    attempts = [s for s in steps if s["kind"] == "execute"]
    assert [s["status"] for s in attempts] == ["cancelled", "done"]
    assert [s["attempt"] for s in attempts] == [1, 2]


def test_invalid_result_after_successful_exit_is_failed_step(project):
    fake(project, t1=[{"text": '```json\n{"protocol":"alfrd.orchestration/1","kind":"result",'
                              '"result":{"status":"maybe"}}\n```'}, result()])
    sid = chat.create_session(project)["id"]
    say(project, sid, "@codex work")
    steps = run(project, sid)["requests"][0]["steps"]
    assert [s["status"] for s in steps if s["kind"] == "execute"] == ["failed", "done"]


def test_waiting_links_are_recorded_and_mail_reply_cannot_complete(project, monkeypatch):
    from alfrd.mailbox import Mailbox, USER
    from tests.test_agent_mailbox import AGENT, permission

    fake(project, plan=plan(task("a")), a=result("blocked", blockers=[{
        "id": "b1", "reason": "needs approval", "needs": "user"}]))
    sid = chat.create_session(project)["id"]
    say(project, sid, "work")
    original = chat._response
    posted = []

    def response(runner, unit):
        text = original(runner, unit)
        if unit["row"] == "r0001.a":
            mail = Mailbox(project, sid, "r0001").send({**AGENT, "unit_id": unit["id"]}, permission())["message"]
            posted.append(mail)
            envelope = json.loads(text.split("```json\n")[1].split("\n```")[0])
            envelope["result"]["blockers"][0]["mailbox_message_id"] = mail["id"]
            return "```json\n" + json.dumps(envelope) + "\n```"
        return text

    monkeypatch.setattr(chat, "_response", response)
    state = run(project, sid)
    before = state["requests"][0]["steps"]
    step = next(s for s in before if s["kind"] == "execute")
    assert step["status"] == "waiting" and step["waiting_on"] == [posted[0]["id"]]
    Mailbox(project, sid, "r0001").reply(posted[0]["id"], USER, {
        "idempotency_key": "approve", "expected_version": 1, "outcome": "approve",
        "action_hash": posted[0]["payload"]["action_hash"]})
    assert chat.session_state(project, sid)["requests"][0]["steps"] == before
