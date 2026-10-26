"""Project chat on the plan runner (alfrd.chat, alfrd.orchestration): protocol, wake-ups, bounds, isolation, recovery.

The agents are fake ``claude`` / ``codex`` / ``agy`` programs (same pattern as the agent-loop tests): they log
each call to ``calls.jsonl`` and answer from ``fake.json`` in the project, so every test counts LLM calls exactly.
"""
from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path

import pytest
import yaml

from alfrd import chat
from alfrd import orchestration as proto
from alfrd.runtime import scheduler

P = proto.PROTOCOL

FAKE = r'''
import json, os, pathlib, re, sys, time
agent = os.path.basename(sys.argv[0])
args = sys.argv[1:]
model = args[args.index("--model") + 1] if "--model" in args else agent
prompt = sys.stdin.read()
spec = json.loads(pathlib.Path("fake.json").read_text())
kind, task = "task", None
if prompt.startswith("# ALFRD chat: plan this request"): kind = "plan"
elif prompt.startswith("# ALFRD chat: fix your plan"): kind = "repair"
elif prompt.startswith("# ALFRD chat: a task is blocked"): kind = "replan"
elif prompt.startswith("# ALFRD chat: a request is stalled"): kind = "check"
elif prompt.startswith("# ALFRD chat: final summary"): kind = "summary"
else: task = re.match(r"# ALFRD task (\S+):", prompt).group(1)
with open("calls.jsonl", "a") as f:
    f.write(json.dumps({"agent": agent, "model": model, "kind": kind, "task": task, "prompt": prompt}) + "\n")
entry = spec.get(task if task else kind, {"text": "ok"})
if isinstance(entry, list):  # one answer per attempt
    n = sum(1 for line in open("calls.jsonl") if json.loads(line)["task" if task else "kind"] == (task or kind))
    entry = entry[min(n, len(entry)) - 1]
hold = entry.get("hold")
while hold and pathlib.Path(hold).exists(): time.sleep(.05)
if entry.get("say"):
    print(entry["say"], file=sys.stderr, flush=True)
if entry.get("exit"):
    print("failing on purpose", file=sys.stderr); sys.exit(entry["exit"])
text = entry["text"] if "text" in entry else "Done.\n```json\n" + json.dumps(entry["json"]) + "\n```\n"
cost = entry.get("cost", spec.get("cost", 0.01))
if agent == "claude":
    for e in [{"type": "system", "subtype": "init", "model": "fake-" + model},
              {"type": "result", "subtype": "success", "result": text, "usage": {"input_tokens": 10, "output_tokens": 5},
               "total_cost_usd": cost}]:
        print(json.dumps(e), flush=True)
elif agent == "codex":
    out = args[args.index("--output-last-message") + 1]
    pathlib.Path(out).write_text(text)
    print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 7, "output_tokens": 3}}))
else:
    print(text)
'''


def plan(*tasks, message=None, decisions=None):
    body = {"protocol": P, "kind": "plan"}
    if message:
        body["message"] = message
    if tasks:
        body["tasks"] = list(tasks)
    if decisions:
        body["decisions"] = decisions
    return {"json": body}


def task(tid, owner="codex", deps=(), **extra):
    return {"id": tid, "title": f"Do {tid}", "owner": owner, "depends_on": list(deps),
            "acceptance_criteria": [f"{tid} works"], **extra}


def result(status="done", summary="did it", **extra):
    return {"json": {"protocol": P, "kind": "result", "result": {"status": status, "summary": summary, **extra}}}


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    bins = tmp_path / "bin"
    bins.mkdir()
    for name in ("claude", "codex", "agy"):
        exe = bins / name
        exe.write_text(f"#!{sys.executable}\n{FAKE}")
        exe.chmod(0o755)
    (root / ".alfrd").mkdir()
    (root / ".alfrd" / "alfrd.yaml").write_text(yaml.safe_dump({
        "version": 1, "name": "chatproj",
        "entrypoint": [{"name": "claude", "cmd": [str(bins / "claude"), "-p"]},
                       {"name": "codex", "cmd": [str(bins / "codex"), "exec", "--output-last-message", "{response_file}", "-"],
                        "output_file": "{response_file}", "output_capture": "file"},
                       {"name": "agy", "cmd": [str(bins / "agy")]}],
        "execution": {"usage_interval": 0},
        "project_settings": {"personas": {"tester": {"label": "Tester", "instructions": "PERSONA-TESTER-TEXT"}}},
        "chat": {"agents": {"sonnet": {"persona": "tester"}}},
    }, sort_keys=False))
    monkeypatch.setattr(scheduler, "POLL", .03)
    monkeypatch.chdir(root)
    return root


def fake(root, **spec):
    (root / "fake.json").write_text(json.dumps(spec))


def configure(root, **chat_settings):
    path = root / ".alfrd" / "alfrd.yaml"
    data = yaml.safe_load(path.read_text())
    data["chat"].update(chat_settings)
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def calls(root):
    path = root / "calls.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def orchestrator_calls(root):
    return [c for c in calls(root) if c["kind"] in ("plan", "repair", "replan", "check", "summary")]


def run(root, sid):
    """One runner pass in-process, as `alfrd plan runner` does it (it stops when nothing is left)."""
    scheduler.Runner(root, f"chat-{sid}").run()
    return chat.session_state(root, sid)


def say(root, sid, text):
    return chat.post_message(root, sid, text, spawn=False)


def events(root, sid):
    path = root / ".alfrd" / "plans" / f"chat-{sid}" / "events.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


# ---------------------------------------------------------------------------
# Protocol


def test_schemas_validate_strictly_and_reject_unknown_versions():
    good = {"protocol": P, "kind": "plan", "tasks": [task("a")]}
    assert proto.validate("envelope", good) is good
    with pytest.raises(proto.ProtocolError, match="unsupported protocol version '2'"):
        proto.validate("envelope", {**good, "protocol": "alfrd.orchestration/2"})
    with pytest.raises(proto.ProtocolError, match="missing \"protocol\""):
        proto.validate("envelope", {"kind": "plan"})
    with pytest.raises(proto.ProtocolError, match="unknown protocol"):
        proto.validate("envelope", {**good, "protocol": "other/1"})
    with pytest.raises(proto.ProtocolError, match="Additional properties"):
        proto.validate("task", {**task("a"), "surprise": 1})
    with pytest.raises(proto.ProtocolError, match="acceptance_criteria"):
        proto.validate("task", {"id": "a", "title": "x", "owner": "codex"})
    with pytest.raises(proto.ProtocolError, match="blockers"):
        proto.validate("result", {"status": "blocked", "summary": "stuck"})
    proto.validate("result", {"status": "done", "summary": "ok", "artifacts": ["a.txt@" + "0" * 64]})
    proto.validate("blocker", {"id": "b1", "reason": "which db?", "options": ["pg", "sqlite"], "needs": "user"})
    with pytest.raises(proto.ProtocolError):
        proto.validate("decision", {"id": "b1"})  # neither choice nor answer
    assert proto.check_graph([task("a", deps=["b"]), task("b", deps=["a"])], agents=["codex"]) == \
        ["dependency cycle: a → b → a"]
    assert "unknown owner" in proto.check_graph([task("a", owner="nobody")], agents=["codex"])[0]
    text = "intro\n```json\n" + json.dumps(good) + "\n```\n"
    assert proto.extract(text)["tasks"][0]["id"] == "a"
    with pytest.raises(proto.ProtocolError, match="no ```json block"):
        proto.extract("no json here")


def test_chat_settings_are_editable_presets_with_bounds(project):
    conf = chat.settings(project)
    assert conf["orchestrator"] == "claude" and conf["agents"]["claude"]["model"] == "opus"
    assert conf["orchestrator_model"] == "sonnet" and conf["escalation_model"] == "opus"
    assert conf["final_summary"] is False and conf["warn_task_cost_usd"] is None
    assert conf["agents"]["claude"]["lean"] is True and "lean" not in conf["agents"]["codex"]
    assert conf["agents"]["sonnet"]["roles"] == ["test planning", "verification", "failure analysis"]
    assert conf["agents"]["codex"]["roles"] == ["design", "development"]
    assert conf["agents"]["agy"]["roles"] == ["research", "image generation"]
    assert conf["bounds"] == {"max_depth": 2, "max_retries": 1, "max_cost_usd": 5.0, "max_seconds": 7200, "max_tasks": 20,
                              "max_replans": 3}
    configure(project, orchestrator="sonnet", bounds={"max_cost_usd": 1}, agents={"agy": None, "sonnet": {"persona": "tester"}})
    conf = chat.settings(project)
    assert conf["orchestrator"] == "sonnet" and "agy" not in conf["agents"] and conf["bounds"]["max_cost_usd"] == 1
    with pytest.raises(Exception, match="not a chat agent"):
        chat.merge_settings({"orchestrator": "ghost"})
    with pytest.raises(Exception, match="max_depth"):
        chat.merge_settings({"bounds": {"max_depth": 0}})


# ---------------------------------------------------------------------------
# Wake-ups: @agent, dependencies, background completion


def test_at_agent_bypasses_the_orchestrator(project):
    fake(project, t1=result(summary="Answered directly"))
    sid = chat.create_session(project)["id"]
    out = say(project, sid, "@codex add a health endpoint")
    assert out["request"]["mode"] == "direct" and out["task"]["owner"] == "codex"
    state = run(project, sid)
    assert orchestrator_calls(project) == []
    assert [c["agent"] for c in calls(project)] == ["codex"]
    assert "add a health endpoint" in calls(project)[0]["prompt"]
    assert state["requests"][0]["status"] == "done" and state["requests"][0]["orchestrator_calls"] == 0
    assert state["messages"][-1]["role"] == "assistant" and state["messages"][-1]["text"] == "Answered directly"
    with pytest.raises(ValueError, match="unknown agent @ghost"):
        say(project, sid, "@ghost hello")


def test_dependency_ready_tasks_launch_without_waking_the_orchestrator(project):
    fake(project, plan=plan(task("a"), task("b", owner="sonnet", deps=["a"]), task("c", owner="agy", deps=["a"]),
                            message="Three steps."),
         a=result(summary="A built"), b=result(summary="B verified"), c=result(summary="C researched"),
         summary={"text": "All three done."})
    configure(project, final_summary=True)
    sid = chat.create_session(project)["id"]
    say(project, sid, "Build, verify and research the feature")
    state = run(project, sid)
    kinds = [c["kind"] if c["kind"] != "task" else c["task"] for c in calls(project)]
    assert kinds[0] == "plan" and kinds[-1] == "summary"
    assert sorted(kinds[1:-1]) == ["a", "b", "c"] and kinds[1] == "a"       # a first, then its dependents
    assert len(orchestrator_calls(project)) == 2                              # one plan + one final summary
    assert {t["status"] for t in state["tasks"]} == {"done"}
    req = state["requests"][0]
    assert req["status"] == "done" and req["orchestrator_calls"] == 2
    assert [m["text"] for m in state["messages"] if m["role"] == "assistant"] == ["Three steps.", "All three done."]
    assert state["usage"]["total_cost_usd"] == pytest.approx(0.01 * 3)       # the claude turns (plan, b, summary) report cost
    g = state["graph"]
    assert {e["from"] for e in g["edges"] if e["kind"] == "depends_on"} == {"r0001.a"} and "graph TD" in g["mermaid"]
    assert sorted((e["from"], e["to"]) for e in g["edges"] if e["kind"] == "planned") == \
        [("r0001.plan", "r0001.a"), ("r0001.plan", "r0001.b"), ("r0001.plan", "r0001.c")]
    kinds_of = {n["id"]: n["kind"] for n in g["nodes"]}
    assert kinds_of["r0001.plan"] == "plan" and kinds_of["r0001.summary-1"] == "summary" and kinds_of["r0001.a"] == "task"
    assert next(n for n in g["nodes"] if n["id"] == "r0001.plan")["title"] == "Plan r0001"
    assert 'n_r0001_plan{{"Plan r0001' in g["mermaid"] and "n_r0001_plan ==> n_r0001_a" in g["mermaid"]
    kinds_seen = {e["kind"] for e in events(project, sid)}
    assert {"task.created", "task.started", "task.finished", "request.finished"} <= kinds_seen


def test_background_completion_needs_no_planner_call_without_a_summary(project):
    configure(project, final_summary=False)
    fake(project, plan=plan(task("slow", owner="agy"), task("after", deps=["slow"])),
         slow={"hold": "hold-slow", **result(summary="slow done")}, after=result())
    (project / "hold-slow").write_text("")
    sid = chat.create_session(project)["id"]
    say(project, sid, "do the slow thing")
    folder = scheduler.PlanDir(project, f"chat-{sid}")
    scheduler.spawn_runner(folder)                       # a real detached runner
    deadline = time.time() + 20
    while time.time() < deadline and not any(c["task"] == "slow" for c in calls(project)):
        time.sleep(.05)
    time.sleep(.5)                                       # the runner waits; nothing is called meanwhile
    assert [c["kind"] for c in orchestrator_calls(project)] == ["plan"]
    (project / "hold-slow").unlink()
    while time.time() < deadline and (folder.runner_alive() or folder.load()["status"] == "running"):
        time.sleep(.05)
    state = chat.session_state(project, sid)
    assert state["requests"][0]["status"] == "done"
    assert [c["kind"] for c in orchestrator_calls(project)] == ["plan"]    # no summary call, no polling call
    assert state["messages"][-1]["agent"] == "alfrd" and "finished" in state["messages"][-1]["text"]


def test_invalid_plan_gets_one_repair_then_an_error(project):
    fake(project, plan={"text": "```json\n{\"protocol\": \"" + P + "\", \"kind\": \"plan\", \"tasks\": [{\"id\": \"x\"}]}\n```"},
         repair={"text": "still not json"})
    sid = chat.create_session(project)["id"]
    say(project, sid, "plan something")
    state = run(project, sid)
    assert [c["kind"] for c in orchestrator_calls(project)] == ["plan", "repair"]
    repair_prompt = [c for c in calls(project) if c["kind"] == "repair"][0]["prompt"]
    assert "acceptance_criteria" in repair_prompt
    assert state["requests"][0]["status"] == "failed"
    assert state["messages"][-1]["role"] == "system" and "invalid after one repair" in state["messages"][-1]["text"]


def test_a_repaired_plan_runs(project):
    fake(project, plan={"text": "```json\n{\"protocol\": \"alfrd.orchestration/9\", \"kind\": \"plan\"}\n```"},
         repair=plan(task("a")), a=result(), summary={"text": "fine"})
    sid = chat.create_session(project)["id"]
    say(project, sid, "plan something")
    state = run(project, sid)
    assert "unsupported protocol version '9'" in [c for c in calls(project) if c["kind"] == "repair"][0]["prompt"]
    assert state["requests"][0]["status"] == "done"
    assert [c["kind"] for c in orchestrator_calls(project)] == ["plan", "repair"]   # final summary is off by default
    assert state["messages"][-1]["agent"] == "alfrd" and "all 1 task(s) done" in state["messages"][-1]["text"]


# ---------------------------------------------------------------------------
# Bounds


def test_orchestrator_runs_on_sonnet_and_escalates_to_opus(project):
    """Plan on orchestrator_model; repair and replan on escalation_model; no summary call by default."""
    blocked = result("blocked", "stuck", blockers=[{"id": "api", "reason": "v1 or v2?", "options": ["v1", "v2"], "needs": "orchestrator"}])
    fake(project, plan={"text": "not a plan"}, repair=plan(task("a"), task("b", owner="sonnet", deps=["a"])),
         a=[blocked, result(summary="api v2 built", artifacts=["notes.txt"])], b=result(summary="verified"),
         replan=plan(decisions=[{"id": "r0001.a.api", "choice": "v2"}]))
    (project / "notes.txt").write_text("v2")
    sid = chat.create_session(project)["id"]
    say(project, sid, "build the api")
    state = run(project, sid)
    used = [(c["kind"], c["model"]) for c in orchestrator_calls(project)]
    assert used == [("plan", "sonnet"), ("repair", "opus"), ("replan", "opus")]
    assert [c["model"] for c in calls(project) if c["task"] == "b"] == ["sonnet"]          # a specialist keeps its own model
    req = state["requests"][0]
    assert req["status"] == "done" and req["orchestrator_calls"] == 3
    final = state["messages"][-1]
    assert final["agent"] == "alfrd" and "all 2 task(s) done" in final["text"] and "`notes.txt`" in final["text"]
    rows = {t["row"]: t for t in state["tasks"]}
    assert rows["r0001.plan"]["model"] == "sonnet" and rows["r0001.repair"]["model"] == "opus"
    configure(project, orchestrator_model=None, escalation_model=None, final_summary=True)
    assert chat.orchestrator_model(chat.settings(project), "plan") is None   # null: the agent's own model (opus)
    with pytest.raises(Exception, match="escalation_model"):
        chat.merge_settings({"escalation_model": ""})


def test_task_cost_warning_flags_without_stopping(project):
    configure(project, warn_task_cost_usd=0.015)
    fake(project, t1={**result(), "cost": 0.02})
    sid = chat.create_session(project)["id"]
    say(project, sid, "@sonnet check it")
    state = run(project, sid)
    task_ = state["tasks"][0]
    assert task_["status"] == "done" and task_["cost_warning"]["bound"] == 0.015 and task_["cost_warning"]["value"] == 0.02
    warnings = [e for e in events(project, sid) if e["kind"] == "budget.warning"]
    assert len(warnings) == 1 and warnings[0]["data"]["limit"] == "warn_task_cost_usd"


def test_retries_are_bounded_and_dependents_are_blocked(project):
    fake(project, plan=plan(task("flaky"), task("next", deps=["flaky"])),
         flaky={"exit": 3}, summary={"text": "it failed"})
    sid = chat.create_session(project)["id"]
    say(project, sid, "try it")
    state = run(project, sid)
    assert [c["task"] for c in calls(project) if c["kind"] == "task"] == ["flaky", "flaky"]   # 1 + max_retries(1)
    assert "RETRY: this is attempt 2" in [c for c in calls(project) if c["task"] == "flaky"][1]["prompt"]
    tasks = {t["row"]: t for t in state["tasks"]}
    assert tasks["r0001.flaky"]["status"] == "failed" and tasks["r0001.flaky"]["attempts"] == 2
    assert tasks["r0001.next"]["status"] == "blocked"
    assert state["requests"][0]["status"] == "failed"
    assert [c["kind"] for c in orchestrator_calls(project)] == ["plan"]
    assert "0 of 2 task(s) done" in state["messages"][-1]["text"]


def test_an_invalid_result_is_a_failed_attempt_within_the_bound(project):
    bad = {"text": "```json\n{\"protocol\": \"" + P + "\", \"kind\": \"result\", \"result\": {\"status\": \"maybe\"}}\n```"}
    fake(project, t1=[bad, result(summary="second try fine")])
    sid = chat.create_session(project)["id"]
    say(project, sid, "@codex do it")
    state = run(project, sid)
    assert [c["task"] for c in calls(project)] == ["t1", "t1"]
    assert state["tasks"][0]["status"] == "done" and state["messages"][-1]["text"] == "second try fine"


def test_delegation_depth_is_bounded(project):
    deep = task("deeper", owner="agy")
    sub = task("sub", owner="agy")
    fake(project, plan=plan(task("top")), top=result(tasks=[sub]), sub=result(tasks=[deep]), summary={"text": "s"})
    sid = chat.create_session(project)["id"]
    say(project, sid, "go deep")
    state = run(project, sid)
    rows = {t["row"]: t for t in state["tasks"] if t["kind"] == "task"}
    assert set(rows) == {"r0001.top", "r0001.top.sub"} and rows["r0001.top.sub"]["depth"] == 2
    assert not any(c["task"] == "deeper" for c in calls(project))
    flagged = [e for e in events(project, sid) if e["kind"] == "budget.exceeded"]
    assert flagged and flagged[0]["data"]["limit"] == "max_depth"
    assert any("delegation depth" in m["text"] for m in state["messages"] if m["role"] == "system")


def test_cost_budget_stops_the_request(project):
    configure(project, bounds={"max_cost_usd": 0.5})
    fake(project, cost=0.4, plan=plan(task("a", owner="sonnet"), task("b", owner="sonnet", deps=["a"])),
         a=result(), b=result())
    sid = chat.create_session(project)["id"]
    say(project, sid, "spend")
    state = run(project, sid)
    assert [c["kind"] if c["kind"] != "task" else c["task"] for c in calls(project)] == ["plan", "a"]
    req = state["requests"][0]
    assert req["status"] == "stopped" and req["stopped_by"] == "max_cost_usd"
    assert {t["row"]: t["status"] for t in state["tasks"]}["r0001.b"] == "stopped"
    assert any(e["kind"] == "budget.exceeded" and e["data"]["limit"] == "max_cost_usd" for e in events(project, sid))
    assert not any(c["kind"] == "summary" for c in calls(project))


def test_wall_clock_budget_stops_running_work(project):
    configure(project, bounds={"max_seconds": 1})
    fake(project, t1={"hold": "hold", **result()})
    (project / "hold").write_text("")
    sid = chat.create_session(project)["id"]
    say(project, sid, "@agy research forever")
    started = time.time()
    state = run(project, sid)
    assert time.time() - started < 20
    assert state["requests"][0]["status"] == "stopped" and state["requests"][0]["stopped_by"] == "max_seconds"
    assert state["tasks"][0]["status"] == "stopped"


# ---------------------------------------------------------------------------
# Blockers and decisions


def test_user_decision_unblocks_a_task_without_an_orchestrator_call(project):
    blocked = result("blocked", "need a choice", blockers=[{"id": "db", "reason": "which db?", "options": ["pg", "sqlite"]}])
    fake(project, t1=[blocked, result(summary="used sqlite")])
    sid = chat.create_session(project)["id"]
    say(project, sid, "@codex set up storage")
    state = run(project, sid)
    assert state["requests"][0]["status"] == "needs_decision"
    assert [b["id"] for b in state["blockers"]] == ["r0001.t1.db"]
    assert any(e["kind"] == "decision.needed" for e in events(project, sid))
    with pytest.raises(ValueError, match="one of"):
        chat.decide(project, sid, "r0001.t1.db", choice="mysql", spawn=False)
    chat.decide(project, sid, "r0001.t1.db", choice="sqlite", spawn=False)
    state = run(project, sid)
    assert state["requests"][0]["status"] == "done" and orchestrator_calls(project) == []
    second = [c for c in calls(project) if c["task"] == "t1"][1]["prompt"]
    assert "which db?" in second and "sqlite" in second


def test_orchestrator_blocker_wakes_the_orchestrator_once(project):
    blocked = result("blocked", "stuck", blockers=[{"id": "api", "reason": "v1 or v2?", "options": ["v1", "v2"], "needs": "orchestrator"}])
    fake(project, plan=plan(task("a")), a=[blocked, result()],
         replan=plan(decisions=[{"id": "r0001.a.api", "choice": "v2"}]), summary={"text": "done"})
    sid = chat.create_session(project)["id"]
    say(project, sid, "build the api")
    state = run(project, sid)
    assert [c["kind"] for c in orchestrator_calls(project)] == ["plan", "replan"]
    assert state["requests"][0]["status"] == "done"
    assert state["decisions"][0]["decided_by"] == "orchestrator" and state["decisions"][0]["choice"] == "v2"


# ---------------------------------------------------------------------------
# Context isolation, recovery, retrieval


def test_missing_expected_outputs_fail_the_attempt_and_retry(project, tmp_path):
    """Seen 2026-10-18: a wrapper ran unrelated work and replied "done" without the task's files."""
    other = tmp_path / "other-repo"
    (other / "out/dir").mkdir(parents=True)
    (other / "out/a.png").write_bytes(b"wrong root")
    manifest = project / ".alfrd/alfrd.yaml"
    data = yaml.safe_load(manifest.read_text())
    data["project_settings"]["agent_access"] = {"folders": [str(other)]}
    manifest.write_text(yaml.safe_dump(data))
    # Long paths must survive the generic retry note's 200-character error preview.
    long_rel = "out/" + "long-name-" * 24 + ".txt"
    fake(project, plan=plan(task("a", expected_outputs=[{"path": "out/a.png", "description": "img"}, "out/dir/", long_rel]),
                            task("b", owner="sonnet", deps=["a"])),
         a=result(), b=result(), summary={"text": "ok"})
    sid = chat.create_session(project)["id"]
    say(project, sid, "make it")
    state = run(project, sid)
    a = next(t for t in state["tasks"] if t["row"].endswith(".a"))
    assert a["status"] == "failed" and a["attempts"] == 2              # one retry, checked in code
    error = a["error"]
    assert error.startswith("expected output(s) missing:")
    attempts = [c["prompt"] for c in calls(project) if c["task"] == "a"]
    assert len(attempts) == 2
    for rel in ("out/a.png", "out/dir/", long_rel):
        assert str(project / rel) in error
        assert str(project / rel) in attempts[0]
        assert str(project / rel) in attempts[1].split("## Previous attempt: missing expected outputs")[1]
    assert f"Same relative path exists under another writable root: {other / 'out/a.png'}" in error
    assert str(other / "out/a.png") in attempts[1]
    assert "ALFRD checks these exact absolute paths; write them there even if you also edit another repo." in attempts[0]
    planner = next(c["prompt"] for c in calls(project) if c["kind"] == "plan")
    assert "Give each expected output as an absolute path" in planner
    assert str(project) in planner
    assert not [c for c in calls(project) if c["task"] == "b"]         # the dependent never ran
    assert chat.missing_outputs(project, ["../escape.txt"]) == ["../escape.txt"]
    (project / "out/dir").mkdir(parents=True)
    (project / "out/a.png").write_bytes(b"png")
    assert chat.missing_outputs(project, [{"path": "out/a.png"}, "out/dir/"]) == []


@pytest.mark.parametrize("output", ["nested/../result.txt", {"path": " result.txt ", "description": "report"}])
def test_expected_prompt_and_check_share_resolution(tmp_path, output):
    lines = "\n".join(chat.expected_output_lines(tmp_path, [output]))
    assert f"`{tmp_path / 'result.txt'}`" in lines
    assert chat.missing_outputs(tmp_path, [output])
    (tmp_path / "result.txt").write_text("done")
    assert chat.missing_outputs(tmp_path, [output]) == []
    assert chat.missing_outputs(tmp_path, ["result.txt/"]) == ["result.txt/"]
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("outside")
    assert chat.missing_outputs(tmp_path, [str(outside)]) == [str(outside)]


def test_agent_prompts_hold_only_their_task(project, tmp_path):
    (project / "spec.md").write_text("the spec")
    (project / "out").mkdir()
    (project / "out/a.txt").write_text("a")
    ref = proto.make_ref(project, "spec.md")
    fake(project, plan=plan(task("a", inputs=[ref], expected_outputs=["out/a.txt"]), task("b", owner="sonnet", deps=["a"])),
         a={"text": "SECRET-TRANSCRIPT-OF-A\n```json\n" + json.dumps(
             {"protocol": P, "kind": "result", "result": {"status": "done", "summary": "A summary", "artifacts": ["spec.md"]}}) + "\n```"},
         b=result(), summary={"text": "ok"})
    sid = chat.create_session(project)["id"]
    say(project, sid, "@agy UNRELATED-HISTORY-MESSAGE")
    run(project, sid)
    say(project, sid, "now the real work")
    run(project, sid)
    prompts = {c["task"] or c["kind"]: c["prompt"] for c in calls(project)}
    assert ref in prompts["a"]
    b = prompts["b"]
    assert "A summary" in b and ref in b                                  # dependency result: summary + refs
    assert "SECRET-TRANSCRIPT-OF-A" not in b                              # never another task's transcript
    assert "UNRELATED-HISTORY-MESSAGE" not in b and "now the real work" not in b   # never the chat history
    assert "PERSONA-TESTER-TEXT" in b                                     # sonnet's persona preset
    assert "UNRELATED-HISTORY-MESSAGE" in prompts["plan"]                 # the planner gets the conversation
    state = chat.session_state(project, sid)
    a = next(t for t in state["tasks"] if t["row"] == "r0002.a")
    assert a["result"]["transcript"]["log"] and a["result"]["transcript"]["prompt"]   # kept for inspection
    reply = [r for r in a["result"]["artifacts"] if "/handoffs/" in r][0]          # a's full reply, by ref
    assert "SECRET-TRANSCRIPT-OF-A" in chat.read_artifact(project, sid, reply)["content"]


def test_recovery_after_the_runner_is_killed(project):
    fake(project, plan=plan(task("a", owner="agy"), task("b", deps=["a"])),
         a={"hold": "hold-a", **result(summary="a ok")}, b=result(summary="b ok"), summary={"text": "recovered"})
    (project / "hold-a").write_text("")
    sid = chat.create_session(project)["id"]
    say(project, sid, "survive a crash")
    folder = scheduler.PlanDir(project, f"chat-{sid}")
    scheduler.spawn_runner(folder)
    deadline = time.time() + 20
    while time.time() < deadline and not any(c["task"] == "a" for c in calls(project)):
        time.sleep(.05)
    pid = folder.load()["runner"]["pid"]
    os.kill(pid, signal.SIGKILL)
    while folder.runner_alive() and time.time() < deadline:
        time.sleep(.05)
    (project / "hold-a").unlink()                         # the agent outlived its runner and now finishes
    before = chat.session_state(project, sid)             # chat state is rebuilt from files alone
    assert before["requests"][0]["status"] == "running"
    assert scheduler.reconcile(project, spawn=False)[0]["action"] == "needs runner"
    state = run(project, sid)                             # a new runner adopts the unit and continues
    assert {t["row"]: t["status"] for t in state["tasks"] if t["kind"] == "task"} == {"r0001.a": "done", "r0001.b": "done"}
    assert state["requests"][0]["status"] == "done"
    assert [c["kind"] if c["kind"] != "task" else c["task"] for c in calls(project)] == ["plan", "a", "b"]


def test_artifact_retrieval_is_confined_hash_checked_and_ranged(project, tmp_path):
    sid = chat.create_session(project)["id"]
    (project / "notes.txt").write_text("0123456789" * 10)
    ref = proto.make_ref(project, "notes.txt")
    part = chat.read_artifact(project, sid, ref, offset=5, length=10)
    assert part["content"] == "5678901234" and part["size"] == 100 and part["truncated"] and not part["eof"]
    (tmp_path / "outside.txt").write_text("secret")
    (project / "link.txt").symlink_to(tmp_path / "outside.txt")
    sha = proto.file_sha256(tmp_path / "outside.txt")
    for bad, status in ((f"../outside.txt@{sha}", 400), (f"/etc/passwd@{sha}", 400), (f"link.txt@{sha}", 403)):
        with pytest.raises(proto.RefError) as exc:
            chat.read_artifact(project, sid, bad)
        assert exc.value.status == status
    (project / "notes.txt").write_text("changed")
    with pytest.raises(proto.RefError) as exc:
        chat.read_artifact(project, sid, ref)
    assert exc.value.status == 409 and "changed since" in str(exc.value)
    big = project / "big.txt"
    big.write_bytes(b"x" * (proto.MAX_READ_BYTES + 10))
    assert len(chat.read_artifact(project, sid, proto.make_ref(project, "big.txt"))["content"]) == proto.MAX_READ_BYTES


def test_chat_plans_stay_out_of_the_workflow_run_list(project):
    sid = chat.create_session(project)["id"]
    assert scheduler.list_plans(project) == []
    assert [p["id"] for p in scheduler.list_plans(project, include_chat=True)] == [f"chat-{sid}"]


# ---------------------------------------------------------------------------
# HTTP API


def test_http_api(project, tmp_path):
    pytest.importorskip("flask")
    from alfrd.gui import create_app
    from alfrd.gui.services import RuntimeCatalogReader
    from alfrd.runtime import RuntimeService, RuntimeStore

    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    service = RuntimeService(store)
    service.register_manifest(project / ".alfrd" / "alfrd.yaml")
    app = create_app({"TESTING": True, "SECRET_KEY": "k", "RUNTIME_SERVICE": service,
                      "CATALOG_READER": RuntimeCatalogReader(service),
                      "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'catalog.sqlite'}"})
    client = app.test_client()
    h = {"X-CSRF-Token": client.get("/api/studio/session").get_json()["csrf_token"]}
    base = f"/api/studio/projects/{service.list_projects()[0].identifier}/chat"
    fake(project, t1=result(summary="api answer"))

    assert client.get(base + "/settings").get_json()["orchestrator"] == "claude"
    assert client.post(base + "/sessions", json={}).status_code == 403              # CSRF gate
    made = client.post(base + "/sessions", json={"title": "API"}, headers=h)
    assert made.status_code == 201
    sid = made.get_json()["session"]["id"]
    assert [s["id"] for s in client.get(base + "/sessions").get_json()["sessions"]] == [sid]
    assert client.post(f"{base}/sessions/{sid}/messages", json={"text": ""}, headers=h).status_code == 400
    sent = client.post(f"{base}/sessions/{sid}/messages", json={"text": "@agy hello"}, headers=h)
    assert sent.status_code == 201 and sent.get_json()["request"]["mode"] == "direct"
    deadline = time.time() + 20
    state = {}
    while time.time() < deadline:
        state = client.get(f"{base}/sessions/{sid}").get_json()
        if state["requests"][0]["status"] == "done":
            break
        time.sleep(.1)
    assert state["requests"][0]["status"] == "done" and state["tasks"][0]["status"] == "done"
    assert {"messages", "tasks", "graph", "blockers", "decisions", "usage", "plan"} <= set(state)
    assert state["tasks"][0]["result"]["at"]  # the UI orders results among the messages by it
    ref = state["tasks"][0]["result"]["artifacts"][0]
    got = client.get(f"{base}/sessions/{sid}/artifacts", query_string={"ref": ref, "length": 5})
    assert got.status_code == 200 and got.get_json()["length"] == 5
    assert client.get(f"{base}/sessions/{sid}/artifacts", query_string={"ref": "../x@" + "0" * 64}).status_code == 400
    assert client.post(f"{base}/sessions/{sid}/tasks/r0001.t1/retry", headers=h).status_code == 400   # it is done
    assert client.post(f"{base}/sessions/{sid}/tasks/nope/cancel", headers=h).status_code == 404
    assert client.post(f"{base}/sessions/{sid}/decisions/nope", json={"choice": "x"}, headers=h).status_code == 404
    assert client.get(f"{base}/sessions/20990101-000000-abcdef").status_code == 404
    # Full transcripts load on demand: attempt list first (sizes only), then one part at a time.
    attempts = client.get(f"{base}/sessions/{sid}/tasks/r0001.t1/transcript").get_json()["attempts"]
    assert len(attempts) == 1 and attempts[0]["response_bytes"] > 0 and "content" not in attempts[0]
    unit = attempts[0]["unit"]
    page = client.get(f"{base}/sessions/{sid}/tasks/r0001.t1/transcript/{unit}/response").get_json()
    assert "api answer" in page["content"] and page["eof"]
    prompt = client.get(f"{base}/sessions/{sid}/tasks/r0001.t1/transcript/{unit}/prompt", query_string={"length": 10}).get_json()
    assert prompt["length"] == 10 and not prompt["eof"]
    assert client.get(f"{base}/sessions/{sid}/tasks/r0001.t1/transcript/{unit}/log").status_code == 404
    assert client.get(f"{base}/sessions/{sid}/tasks/r0001.t1/transcript/..%2F..%2Fx/response").status_code == 404
    assert client.get(f"{base}/sessions/{sid}/tasks/nope/transcript").status_code == 404
    log = state["tasks"][0]["logs"][0]
    assert log["unit"] == unit and log["log"].endswith(f"{unit}.log") and log["handoff"].endswith(f"handoffs/{unit}")
    # Promote to workflow: preview (no write), save with the preview's base_hash, a stale preview → 409.
    manifest = project / ".alfrd" / "alfrd.yaml"
    before = manifest.read_text()
    preview = client.post(f"{base}/sessions/{sid}/requests/r0001/promote", json={}, headers=h)
    assert preview.status_code == 200 and manifest.read_text() == before
    body = preview.get_json()
    assert [s_["id"] for s_ in body["steps"]] == ["r0001-t1"] and "+  r0001-t1:" in body["diff"]
    manifest.write_text(before + "\n# edited elsewhere\n")
    stale = client.post(f"{base}/sessions/{sid}/requests/r0001/promote", json={"preview": False, "base_hash": body["base_hash"]}, headers=h)
    assert stale.status_code == 409 and "edited elsewhere" in stale.get_json()["current_text"]
    fresh = client.post(f"{base}/sessions/{sid}/requests/r0001/promote", json={}, headers=h).get_json()
    saved = client.post(f"{base}/sessions/{sid}/requests/r0001/promote", json={"preview": False, "base_hash": fresh["base_hash"]}, headers=h)
    assert saved.status_code == 200 and "r0001-t1" in manifest.read_text()


def test_promote_a_request_to_workflow_steps(project):
    from alfrd import history
    from alfrd.execution import load_execution

    fake(project, plan=plan(task("impl", expected_outputs=["src/x.py"]), task("verify", owner="sonnet", deps=["impl"]),
                            task("docs", owner="agy")),
         impl=result(summary="built"), verify=result(summary="checked"), docs=result(summary="written"))
    (project / "src").mkdir()
    (project / "src" / "x.py").write_text("x = 1\n")
    manifest = project / ".alfrd" / "alfrd.yaml"
    data = yaml.safe_load(manifest.read_text())
    data["workflows"] = [{"name": "main", "steps": ["build"]}]
    data["steps"] = {"build": {"cmd": ["true"]}}
    manifest.write_text("# keep this comment\n" + yaml.safe_dump(data, sort_keys=False))
    sid = chat.create_session(project)["id"]
    say(project, sid, "build, verify, document")
    run(project, sid)
    text = manifest.read_text()
    preview = chat.promotion(project, sid, "r0001")
    assert manifest.read_text() == text                                     # a preview writes nothing
    steps = {s_["task"]: s_ for s_ in preview["steps"]}
    assert [s_["id"] for s_ in preview["steps"]] == ["r0001-impl", "r0001-verify", "r0001-docs"]
    assert steps["r0001.verify"]["depends_on"] == ["r0001-impl"] and steps["r0001.impl"]["depends_on"] == []
    assert steps["r0001.verify"]["model"] == "sonnet" and steps["r0001.impl"]["entrypoint"] == "codex"
    assert "# keep this comment" in preview["text"] and preview["base_hash"] == history.text_hash(text)
    out = chat.promote(project, sid, "r0001", base_hash=preview["base_hash"])
    assert [s_["id"] for s_ in out["steps"]] == ["r0001-impl", "r0001-verify", "r0001-docs"]
    saved = yaml.safe_load(manifest.read_text())
    assert saved["workflows"][0]["steps"] == ["build", "r0001-impl", "r0001-verify", "r0001-docs"]
    assert saved["steps"]["r0001-verify"]["depends_on"] == ["r0001-impl"] and saved["steps"]["r0001-impl"]["depends_on"] == []
    assert saved["steps"]["r0001-impl"]["outputs"] == ["src/x.py"]
    impl_prompt = (project / saved["steps"]["r0001-impl"]["stdin_file"]).read_text()
    assert f"`{project / 'src/x.py'}`" in impl_prompt
    assert "ALFRD checks these exact absolute paths" in impl_prompt
    prompt = project / saved["steps"]["r0001-verify"]["stdin_file"]
    assert "# Do verify" in prompt.read_text() and "verify works" in prompt.read_text() and "r0001-impl" in prompt.read_text()
    cfg = load_execution(project)
    assert cfg.step_ids == ["build", "r0001-impl", "r0001-verify", "r0001-docs"]
    assert cfg.step("r0001-verify").model == "sonnet" and cfg.step("r0001-verify").agent
    assert chat.session_state(project, sid)["requests"][0]["promoted"][0]["steps"][0] == "r0001-impl"
    again = chat.promotion(project, sid, "r0001")                           # promoting twice gets fresh step names
    assert [s_["id"] for s_ in again["steps"]][0] == "r0001-impl-2"
    with pytest.raises(history.Conflict):
        chat.promote(project, sid, "r0001", base_hash=preview["base_hash"])   # alfrd.yaml changed since that preview
    data = yaml.safe_load(manifest.read_text())
    data["workflows"][0]["repeat"] = {"iterations": 2, "sequence": ["claude"]}
    manifest.write_text(yaml.safe_dump(data, sort_keys=False))
    with pytest.raises(ValueError, match="agent loop"):
        chat.promotion(project, sid, "r0001")


def test_cancel_and_retry_a_task(project):
    configure(project, final_summary=False)
    fake(project, plan=plan(task("a"), task("b", deps=["a"])), a={"exit": 2}, b=result())
    sid = chat.create_session(project)["id"]
    say(project, sid, "work")
    state = run(project, sid)
    assert {t["row"]: t["status"] for t in state["tasks"] if t["kind"] == "task"} == {"r0001.a": "failed", "r0001.b": "blocked"}
    fake(project, plan=plan(task("a"), task("b", deps=["a"])), a=result(), b=result())
    out = chat.retry_task(project, sid, "r0001.a", spawn=False)
    assert out["requeued"] == ["r0001.a", "r0001.b"]
    state = run(project, sid)
    assert state["requests"][0]["status"] == "done" and [c["kind"] for c in orchestrator_calls(project)] == ["plan"]

    say(project, sid, "@codex something to cancel")
    assert chat.cancel_task(project, sid, "r0002.t1", spawn=False)["requested"] == "cancelled"
    state = run(project, sid)
    assert state["requests"][1]["status"] == "failed" and not any(c["task"] == "t1" for c in calls(project))
    with pytest.raises(ValueError, match="already cancelled"):
        chat.cancel_task(project, sid, "r0002.t1", spawn=False)
