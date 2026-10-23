"""Permission blockers and the replan loop (2026-10-23 incident: a Claude task without access to the project
folder was relaunched 17 times with the same argv while the orchestrator replanned 12 times).

A decision now carries an action ALFRD applies itself (grant / reassign / cancel); permission blockers go to the
user; the same blocker with the same access is not relaunched; replans per request are bounded.
"""
# ruff: noqa: F811 - `project` is the imported fixture, taken by name
from __future__ import annotations

import os

import pytest
from test_chat_orchestration import (
    calls,
    configure,
    events,
    fake,
    orchestrator_calls,
    plan,
    project,  # noqa: F401 - the fixture (tests below take it by name)
    result,
    run,
    say,
    task,
)

from alfrd import chat
from alfrd.agent_io import access_folders, agent_command, claude_bash_rules


def units(root, sid):
    return chat._plan_folder(chat.session(root, sid)).units()


def task_units(root, sid, row):
    return [u for u in units(root, sid) if u.get("row") == row]


def denied(path, **extra):
    return result("blocked", "no access", blockers=[{
        "id": "b1", "reason": f"Listing {path} was blocked: it is outside the allowed working directories.",
        "question": "Grant access?", "options": ["Try again"], "needs": "orchestrator", **extra}])


def pending(state):
    return [d for d in state["decisions"] if d["status"] == "pending"]


def test_a_permission_blocker_asks_the_user_and_a_grant_reaches_the_next_launch(project, tmp_path):
    outside = tmp_path / "site"
    outside.mkdir()
    fake(project, plan=plan(task("t1", owner="claude")), t1=[denied(outside), result()])
    sid = chat.create_session(project)["id"]
    say(project, sid, "edit the site")
    state = run(project, sid)
    # needs "orchestrator" in the blocker, but the orchestrator cannot change a sandbox: no replan call
    assert [c["kind"] for c in orchestrator_calls(project)] == ["plan"]
    [d] = pending(state)
    assert d["needs"] == "user" and d["kind"] == "permission"
    grant = next(o for o in d["options"] if o.startswith("Grant this task access"))
    assert str(outside) in grant
    assert "Reassign this task to @codex" in d["options"] and "Cancel this task" in d["options"]
    assert not any("@sonnet" in o for o in d["options"])  # another Claude has the same sandbox
    first = task_units(project, sid, "r0001.t1")[0]["argv"]
    assert "Bash(alfrd mailbox *)" in first[first.index("--allowedTools") + 1]
    assert str(outside) not in first

    chat.decide(project, sid, d["id"], choice=grant, spawn=False)
    state = run(project, sid)
    assert state["requests"][0]["status"] == "done"
    second = task_units(project, sid, "r0001.t1")[1]
    assert second["argv"][second["argv"].index("--add-dir") + 1] == str(outside)
    log = (project / second["log"]).read_text()
    assert "# access added for this task: folders " + str(outside) in log
    assert any(e["kind"] == "task.access_granted" for e in events(project, sid))
    prompt = [c for c in calls(project) if c["task"] == "t1"][1]["prompt"]
    assert "## Your access" in prompt and "Granted to this task by the user: " + str(outside) in prompt


def test_reassign_moves_the_task_and_cancel_ends_it(project, tmp_path):
    fake(project, plan=plan(task("t1", owner="claude"), task("t2", owner="claude"), task("t3", deps=["t2"])),
         t1=denied(tmp_path), t2=denied(tmp_path), t3=result())
    sid = chat.create_session(project)["id"]
    say(project, sid, "two things")
    state = run(project, sid)
    by_task = {d["task"]: d for d in pending(state)}
    chat.decide(project, sid, by_task["r0001.t1"]["id"], choice="Reassign this task to @codex", spawn=False)
    chat.decide(project, sid, by_task["r0001.t2"]["id"], choice="Cancel this task", spawn=False)
    fake(project, t1=result(summary="codex did it"), t3=result())
    state = run(project, sid)
    recs = {t["row"]: t for t in state["tasks"]}
    assert recs["r0001.t1"]["status"] == "done" and recs["r0001.t1"]["owner"] == "codex"
    assert [c["agent"] for c in calls(project) if c["task"] == "t1"] == ["claude", "codex"]
    assert recs["r0001.t2"]["status"] == "cancelled" and recs["r0001.t3"]["status"] == "blocked"
    assert sum(c["task"] == "t2" for c in calls(project)) == 1  # t2 never ran again
    assert state["requests"][0]["status"] == "failed"
    assert [c["kind"] for c in orchestrator_calls(project)] == ["plan"]


def test_an_agent_option_that_says_cancel_cancels(project):
    blocked = result("blocked", "unsure", blockers=[{"id": "q", "reason": "which one?", "options": ["Use A", "Cancel quick win #8"]}])
    fake(project, t1=blocked)
    sid = chat.create_session(project)["id"]
    say(project, sid, "@codex quick win")
    state = run(project, sid)
    chat.decide(project, sid, pending(state)[0]["id"], choice="Cancel quick win #8", spawn=False)
    state = run(project, sid)
    assert state["tasks"][0]["status"] == "cancelled" and len(calls(project)) == 1


def test_the_same_blocker_with_the_same_access_is_not_replanned_again(project):
    stuck = result("blocked", "stuck", blockers=[{"id": "api", "reason": "v1 or v2?", "options": ["v1", "v2"], "needs": "orchestrator"}])
    fake(project, plan=plan(task("a")), a=[stuck, stuck, result()],
         replan=plan(decisions=[{"id": "r0001.a.api", "choice": "v2"}]))
    sid = chat.create_session(project)["id"]
    say(project, sid, "build the api")
    state = run(project, sid)
    assert [c["kind"] for c in orchestrator_calls(project)] == ["plan", "replan"]  # not a second replan
    [d] = pending(state)
    assert d["needs"] == "user" and d["repeat"] == 2 and d["id"] != "r0001.a.api"  # the first decision is kept
    assert any(m.get("kind") == "stuck" for m in state["messages"])
    chat.decide(project, sid, d["id"], choice="v1", spawn=False)  # not a permission blocker: a re-run is allowed
    assert run(project, sid)["requests"][0]["status"] == "done"
    prompt = [c for c in calls(project) if c["task"] == "a"][2]["prompt"]
    assert "→ v1" in prompt and "→ v2" not in prompt  # only the latest decision per blocker


def test_replans_per_request_are_bounded(project):
    configure(project, bounds={"max_replans": 0})
    stuck = result("blocked", "stuck", blockers=[{"id": "api", "reason": "v1 or v2?", "options": ["v1", "v2"], "needs": "orchestrator"}])
    fake(project, plan=plan(task("a")), a=[stuck, result()])
    sid = chat.create_session(project)["id"]
    say(project, sid, "build the api")
    state = run(project, sid)
    assert [c["kind"] for c in orchestrator_calls(project)] == ["plan"]
    assert pending(state)[0]["needs"] == "user"
    assert any(e["kind"] == "budget.exceeded" and e["data"].get("limit") == "max_replans" for e in events(project, sid))


def test_an_unchanged_rerun_of_a_repeated_permission_blocker_is_refused(project, tmp_path):
    fake(project, t1=[denied(tmp_path), denied(tmp_path), result()])
    sid = chat.create_session(project)["id"]
    say(project, sid, "@claude edit it")
    state = run(project, sid)
    chat.decide(project, sid, pending(state)[0]["id"], choice="Try again", spawn=False)  # first time: allowed
    state = run(project, sid)
    [d] = pending(state)
    assert d["repeat"] == 2
    with pytest.raises(ValueError, match="same blocker again"):
        chat.decide(project, sid, d["id"], choice="Try again", spawn=False)
    with pytest.raises(ValueError, match="same blocker again"):
        chat.decide(project, sid, d["id"], answer="please just retry", spawn=False)
    grant = next(o for o in d["options"] if o.startswith("Grant"))
    chat.decide(project, sid, d["id"], choice=grant, spawn=False)
    assert run(project, sid)["requests"][0]["status"] == "done"


def test_the_orchestrator_can_reassign_but_not_grant(project, tmp_path):
    stuck = result("blocked", "stuck", blockers=[{"id": "x", "reason": "the parser is out of my depth", "needs": "orchestrator"}])
    sid = chat.create_session(project)["id"]
    fake(project, plan=plan(task("a", owner="sonnet")), a=[stuck, result()],
         replan=plan(decisions=[{"id": "r0001.a.x", "reassign": "codex"}]))
    say(project, sid, "parse it")
    state = run(project, sid)
    rec = next(t for t in state["tasks"] if t["row"] == "r0001.a")
    assert rec["owner"] == "codex" and rec["status"] == "done" and rec["reassigned_from"] == ["sonnet"]

    # a grant chosen by the orchestrator stays pending for the user
    fake(project, plan=plan(task("b", owner="claude")), b=denied(tmp_path, needs="orchestrator"))
    say(project, sid, "edit outside")
    state = run(project, sid)
    d = next(d for d in pending(state) if d["task"] == "r0002.b")
    grant = next(o for o in d["options"] if o.startswith("Grant"))
    store = chat.session(project, sid)
    with store.lock():
        out = chat._resolve(None, store, store.request("r0002"), d["id"], grant, None, "orchestrator")
    assert out["status"] == "pending" and not store.task("r0002.b").get("access_extra")


def test_a_grant_must_name_existing_absolute_folders(project, tmp_path):
    fake(project, t1=denied(tmp_path, requires={"folders": [str(tmp_path / "missing")]}))
    sid = chat.create_session(project)["id"]
    say(project, sid, "@claude edit")
    state = run(project, sid)
    [d] = pending(state)
    assert d["kind"] == "permission" and not any(o.startswith("Grant") for o in d["options"])  # nothing grantable
    store = chat.session(project, sid)
    rec = store.task(d["task"])
    with pytest.raises(ValueError, match="not an existing absolute folder"):
        chat._apply_grant(store, rec, {"folders": ["relative/dir"]})


def test_bash_rules_widen_bare_patterns_and_add_read_only_defaults():
    assert claude_bash_rules(["npm", "git apply", "pytest *", "rtk:*"]) == [
        "Bash(npm)", "Bash(npm *)", "Bash(git apply)", "Bash(git apply *)", "Bash(pytest *)", "Bash(rtk:*)"]
    assert "Bash(head *)" in claude_bash_rules(["ls *"], readonly=True)
    argv = agent_command(["claude", "-p"], access={"bash_commands": ["ls *"], "default_readonly": False})[0]
    assert argv[argv.index("--allowedTools") + 1] == "Bash(ls *)"
    again = agent_command(list(argv), access={"bash_commands": ["ls *", "alfrd mailbox *"]})[0]
    assert again[again.index("--allowedTools") + 1].split(",").count("Bash(ls *)") == 1


def test_access_folders_expand_globs_and_keep_both_symlink_forms(tmp_path):
    real = tmp_path / "real"
    (real / "a").mkdir(parents=True)
    (real / "b").mkdir()
    link = tmp_path / "link"
    os.symlink(real, link)
    out = access_folders(tmp_path, [str(link / "*"), "nothing/*", "real/a"])
    assert out == [str(link / "a"), str((real / "a").resolve()), str(link / "b"), str((real / "b").resolve())]


def test_the_2026_10_23_loop_ends_in_one_decision_per_task(project, tmp_path):
    """r0002 replayed: two Claude tasks without the project folder, verify and update-report after them.
    Before the fix: 12 replans and 17 launches of one task. Now: no replan, one user decision each, one
    more launch each with the folder, then the rest runs."""
    site = tmp_path / "radathomeindia"
    site.mkdir()
    blocked = lambda: denied(site)  # the agent's wording; needs=orchestrator as in the incident
    fake(project, plan=plan(task("nginx-cache", owner="claude"), task("citation-headers", owner="sonnet"),
                            task("verify", owner="sonnet", deps=["nginx-cache", "citation-headers"]),
                            task("update-report", owner="claude", deps=["verify"])),
         **{"nginx-cache": [blocked(), result()], "citation-headers": [blocked(), result()],
            "verify": result(), "update-report": result()},
         replan=plan(decisions=[{"id": "r0001.nginx-cache.b1", "choice": "Try again"}]))
    sid = chat.create_session(project)["id"]
    say(project, sid, "finish the quick wins")
    state = run(project, sid)
    assert [c["kind"] for c in orchestrator_calls(project)] == ["plan"]
    decisions = pending(state)
    assert sorted(d["task"] for d in decisions) == ["r0001.citation-headers", "r0001.nginx-cache"]
    assert all(d["needs"] == "user" for d in decisions)
    for d in decisions:
        chat.decide(project, sid, d["id"], choice=next(o for o in d["options"] if o.startswith("Grant")), spawn=False)
    state = run(project, sid)
    assert state["requests"][0]["status"] == "done"
    launches = {}
    for c in calls(project):
        launches[c["task"]] = launches.get(c["task"], 0) + 1
    assert launches == {None: 1, "nginx-cache": 2, "citation-headers": 2, "verify": 1, "update-report": 1}
    for row in ("r0001.nginx-cache", "r0001.citation-headers"):
        argv = task_units(project, sid, row)[-1]["argv"]
        assert argv[argv.index("--add-dir") + 1] == str(site)


def test_review_edge_cases(project, tmp_path):
    """Third repeat keeps every earlier decision; only "cancel…" options cancel; a bad orchestrator decision is
    reported instead of crashing the runner; a grant is exactly what its label shows."""
    stuck = result("blocked", "stuck", blockers=[{"id": "b1", "reason": "v1 or v2?",
                                                  "options": ["v1", "Skip the flaky test and continue"]}])
    fake(project, t1=[stuck, stuck, stuck, result()])
    sid = chat.create_session(project)["id"]
    say(project, sid, "@codex go")
    for choice in ("v1", "v1"):
        state = run(project, sid)
        chat.decide(project, sid, pending(state)[0]["id"], choice=choice, spawn=False)
    state = run(project, sid)
    by_id = {d["id"]: d for d in state["decisions"]}
    assert {k: d["status"] for k, d in by_id.items()} == {"r0001.t1.b1": "resolved", "r0001.t1.b1.2": "resolved",
                                                         "r0001.t1.b1.3": "pending"}
    assert "Skip the flaky test and continue" not in (by_id["r0001.t1.b1.3"].get("actions") or {})

    other = result("blocked", "stuck", blockers=[{"id": "x", "reason": "which parser?", "needs": "orchestrator"}])
    fake(project, plan=plan(task("a")), a=other, replan=plan(decisions=[{"id": "r0002.a.x", "reassign": "codex"}]))
    say(project, sid, "parse")
    state = run(project, sid)  # codex owns a: reassigning to itself is refused, not a crash
    d = next(d for d in state["decisions"] if d["id"] == "r0002.a.x")
    assert d["status"] == "pending" and d["needs"] == "user"
    assert any("was not applied" in (m.get("text") or "") for m in state["messages"])

    many = [tmp_path / f"folder-with-a-rather-long-name-{i:02d}" for i in range(12)]
    for f in many:
        f.mkdir()
    fake(project, t1=denied(tmp_path, requires={"folders": [str(f) for f in many], "bash_commands": ["*", "make *"]}))
    say(project, sid, "@claude build")
    state = run(project, sid)
    d = next(d for d in pending(state) if d["task"] == "r0003.t1")
    label = next(o for o in d["options"] if o.startswith("Grant"))
    grant = d["actions"][label]["grant"]
    assert len(label) <= 300 and all(f in label for f in grant["folders"]) and 0 < len(grant["folders"]) < 12
    assert "*" not in grant["bash_commands"]
