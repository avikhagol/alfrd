"""Decisions the user hands to the orchestrator: ``chat.delegate_decisions`` and "let the orchestrator decide"."""
# ruff: noqa: F811 - `project` is the imported fixture, taken by name
from __future__ import annotations

import pytest
from test_chat_orchestration import (
    configure,
    fake,
    orchestrator_calls,
    plan,
    project,  # noqa: F401 - the fixture (tests below take it by name)
    result,
    run,
    say,
    task,
)
from test_chat_permissions import denied, pending

from alfrd import chat
from alfrd.execution import ExecutionError

QUESTION = result("blocked", "unsure", blockers=[{"id": "api", "reason": "v1 or v2?", "options": ["v1", "v2"]}])


def test_off_by_default_a_question_waits_for_the_user(project):
    fake(project, plan=plan(task("a")), a=[QUESTION, result()])
    sid = chat.create_session(project)["id"]
    say(project, sid, "build the api")
    state = run(project, sid)
    [d] = pending(state)
    assert d["needs"] == "user" and "delegated" not in d
    assert [c["kind"] for c in orchestrator_calls(project)] == ["plan"]


def test_delegated_questions_are_decided_by_the_orchestrator(project):
    configure(project, delegate_decisions="questions")
    fake(project, plan=plan(task("a")), a=[QUESTION, result()],
         replan=plan(decisions=[{"id": "r0001.a.api", "choice": "v2"}]))
    sid = chat.create_session(project)["id"]
    say(project, sid, "build the api")
    state = run(project, sid)
    assert [c["kind"] for c in orchestrator_calls(project)] == ["plan", "replan"]
    replan = orchestrator_calls(project)[1]["prompt"]
    assert "The user delegated `r0001.a.api` to you" in replan
    assert state["requests"][0]["status"] == "done"
    [d] = state["decisions"]
    assert d["status"] == "resolved" and d["decided_by"] == "orchestrator" and d["delegated"] == "setting"
    note = next(m for m in state["messages"] if m.get("kind") == "delegated_decision")
    assert "v1 or v2?" in note["text"] and "→ v2" in note["text"]


def test_questions_mode_keeps_permission_blockers_with_the_user(project, tmp_path):
    configure(project, delegate_decisions="questions")
    fake(project, plan=plan(task("t1", owner="claude")), t1=denied(tmp_path))
    sid = chat.create_session(project)["id"]
    say(project, sid, "edit outside")
    state = run(project, sid)
    [d] = pending(state)
    assert d["kind"] == "permission" and d["needs"] == "user" and "delegated" not in d
    assert [c["kind"] for c in orchestrator_calls(project)] == ["plan"]


def test_all_mode_lets_the_orchestrator_grant_a_delegated_permission(project, tmp_path):
    configure(project, delegate_decisions="all")
    fake(project, plan=plan(task("t1", owner="claude")), t1=denied(tmp_path),
         replan=plan(message="waiting"))  # no decision: it comes back to the user
    sid = chat.create_session(project)["id"]
    say(project, sid, "edit outside")
    state = run(project, sid)
    assert [c["kind"] for c in orchestrator_calls(project)] == ["plan", "replan"]
    [d] = pending(state)
    assert d["kind"] == "permission" and d["delegated"] == "setting" and d["needs"] == "user"
    grant = next(o for o in d["options"] if o.startswith("Grant"))
    store = chat.session(project, sid)
    with store.lock():
        out = chat._resolve(None, store, store.request("r0001"), d["id"], grant, None, "orchestrator")
    assert out["status"] == "resolved" and store.task("r0001.t1").get("access_extra")


def test_the_user_can_hand_one_decision_to_the_orchestrator(project):
    fake(project, plan=plan(task("a")), a=[QUESTION, result()],
         replan=plan(decisions=[{"id": "r0001.a.api", "choice": "v1"}]))
    sid = chat.create_session(project)["id"]
    say(project, sid, "build the api")
    [d] = pending(run(project, sid))
    out = chat.delegate_decision(project, sid, d["id"], spawn=False)
    assert out["needs"] == "orchestrator" and out["delegated"] == "user"
    state = run(project, sid)
    assert [c["kind"] for c in orchestrator_calls(project)] == ["plan", "replan"]
    assert state["requests"][0]["status"] == "done"
    assert state["decisions"][0]["choice"] == "v1" and state["decisions"][0]["decided_by"] == "orchestrator"
    with pytest.raises(ValueError, match="is resolved"):
        chat.delegate_decision(project, sid, d["id"], spawn=False)


def test_a_delegated_decision_past_max_replans_comes_back_to_the_user(project):
    configure(project, bounds={"max_replans": 0})
    fake(project, plan=plan(task("a")), a=[QUESTION, result()])
    sid = chat.create_session(project)["id"]
    say(project, sid, "build the api")
    [d] = pending(run(project, sid))
    out = chat.delegate_decision(project, sid, d["id"], spawn=False)
    assert out["needs"] == "user" and out["status"] == "pending"
    assert [c["kind"] for c in orchestrator_calls(project)] == ["plan"]


def test_an_unknown_delegate_mode_is_refused():
    with pytest.raises(ExecutionError, match="delegate_decisions"):
        chat.merge_settings({"delegate_decisions": "sometimes"})
