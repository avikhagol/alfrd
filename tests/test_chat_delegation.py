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


@pytest.mark.parametrize("mode,manual", [("questions", False), ("all", False), ("off", True)])
@pytest.mark.parametrize("response", [{"answer": "Use v2"}, {"choice": "a"}])
def test_mailbox_ask_delegation_delivers_audited_answer(project, monkeypatch, mode, manual, response):
    from alfrd.mailbox import Mailbox, USER
    configure(project, delegate_decisions=mode)
    sid, posted = mailbox_blocker(project, monkeypatch)
    fake(project, plan=plan(task("a")), a=[QUESTION, result()],
         replan=plan(decisions=[{"id": "r0001.a.api", **response}]))
    state = run(project, sid)
    if manual:
        chat.delegate_decision(project, sid, pending(state)[0]["id"], spawn=False)
        state = run(project, sid)
    assert state["requests"][0]["status"] == "done"
    [d] = state["decisions"]
    assert d["status"] == "resolved" and d["decided_by"] == "orchestrator"
    reply = Mailbox(project, sid, "r0001").status(posted[0], USER)["reply"]
    assert reply["sender"] == {"kind": "orchestrator", "on_behalf_of": "user",
                               "delegated": "user" if manual else "setting"}
    assert reply["payload"]["answer"] == response.get("answer")
    assert reply["payload"]["choice_id"] == response.get("choice")
    assert any(m.get("kind") == "delegated_decision" for m in state["messages"])
    store = chat.session(project, sid)
    prompt = chat.task_prompt(store, chat.settings(project), store.task("r0001.a"))
    assert response.get("answer", '"a"') in prompt


def mailbox_blocker(project, monkeypatch, permission=False):
    import json
    from alfrd.mailbox import Mailbox
    from test_agent_mailbox import AGENT, question, permission as prm
    sid = chat.create_session(project)["id"]
    say(project, sid, "build the api")
    original = chat._response
    posted = []

    def response(runner, unit):
        text = original(runner, unit)
        if unit["row"] == "r0001.a" and not posted:
            mail = Mailbox(project, sid, "r0001").send(
                {**AGENT, "unit_id": unit["id"]}, prm() if permission else question())["message"]
            posted.append(mail["id"])
            envelope = json.loads(text.split("```json\n")[1].split("\n```")[0])
            envelope["result"]["blockers"][0]["mailbox_message_id"] = mail["id"]
            return "```json\n" + json.dumps(envelope) + "\n```"
        return text

    monkeypatch.setattr(chat, "_response", response)
    return sid, posted


@pytest.mark.parametrize("explicit_permission", [False, True])
def test_mailbox_prm_is_human_only_even_with_all(project, monkeypatch, explicit_permission):
    from alfrd.mailbox import Mailbox, USER
    configure(project, delegate_decisions="all")
    sid, posted = mailbox_blocker(project, monkeypatch, permission=True)
    blocked = QUESTION
    if explicit_permission:
        blocked = result("blocked", blockers=[{"id": "api", "reason": "Approve publish", "kind": "permission"}])
    fake(project, plan=plan(task("a")), a=blocked)
    state = run(project, sid)
    [d] = pending(state)
    assert d["needs"] == "user" and not d.get("delegated")
    assert [c["kind"] for c in orchestrator_calls(project)] == ["plan"]
    with pytest.raises(ValueError, match="only you"):
        chat.delegate_decision(project, sid, d["id"], spawn=False)
    store = chat.session(project, sid)
    chat._resolve(None, store, store.request("r0001"), d["id"], "approve", None, "orchestrator")
    assert Mailbox(project, sid, "r0001").status(posted[0], USER)["state"] == "pending"


def test_mailbox_delegation_respects_max_replans(project, monkeypatch):
    configure(project, delegate_decisions="questions", bounds={"max_replans": 0})
    sid, _ = mailbox_blocker(project, monkeypatch)
    fake(project, plan=plan(task("a")), a=QUESTION)
    [d] = pending(run(project, sid))
    assert d["needs"] == "user" and d["delegated"] == "setting"
    assert [c["kind"] for c in orchestrator_calls(project)] == ["plan"]


@pytest.mark.parametrize("permission,mode", [(True, "all"), (False, "questions")])
def test_a_mailbox_linked_access_blocker_keeps_the_grant_with_the_user(project, monkeypatch, tmp_path, permission, mode):
    configure(project, delegate_decisions=mode)
    outside = tmp_path / "site"
    outside.mkdir()
    sid, _ = mailbox_blocker(project, monkeypatch, permission=permission)
    fake(project, plan=plan(task("a")), a=denied(outside, requires={"folders": [str(outside)]}))
    [d] = pending(run(project, sid))
    # Only a re-launch widens the sandbox: the grant is decided here, not by a mailbox reply, and with
    # an action-hash approval involved a person decides it even with `all`.
    assert d["kind"] == "permission" and "mailbox_message_id" not in d
    assert d["needs"] == "user" and "delegated" not in d
    assert [c["kind"] for c in orchestrator_calls(project)] == ["plan"]
    grant = next(o for o in d["options"] if o.startswith("Grant this task access"))
    chat.decide(project, sid, d["id"], choice=grant, spawn=False)
    assert str(outside) in chat.session(project, sid).task("r0001.a")["access_extra"]["folders"]


def test_questions_mode_keeps_a_mailbox_linked_permission_blocker_with_the_user(project, monkeypatch):
    configure(project, delegate_decisions="questions")
    sid, _ = mailbox_blocker(project, monkeypatch)
    fake(project, plan=plan(task("a")),
         a=result("blocked", blockers=[{"id": "api", "reason": "Need deploy rights", "kind": "permission"}]))
    [d] = pending(run(project, sid))
    assert d["needs"] == "user" and "delegated" not in d
    assert [c["kind"] for c in orchestrator_calls(project)] == ["plan"]
