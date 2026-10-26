"""Expected outputs frozen as absolute paths when a task is planned (Epic B1)."""
# ruff: noqa: F811 - `project` is the imported fixture, taken by name
from __future__ import annotations

import pytest
import yaml
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

from test_agent_mailbox import AGENT, box  # noqa: F401 - the fixture

from alfrd import chat
from alfrd import orchestration as proto
from alfrd.mailbox import MailboxError
from alfrd.mailbox_codes import CODES


def _allow(project, folder):
    manifest = project / ".alfrd/alfrd.yaml"
    data = yaml.safe_load(manifest.read_text())
    data["project_settings"]["agent_access"] = {"folders": [str(folder)]}
    manifest.write_text(yaml.safe_dump(data))


def _rec(project, sid, tid):
    state = chat.session_state(project, sid)
    row = next(t["row"] for t in state["tasks"] if t["row"].endswith(f".{tid}"))
    return chat.Session(project, sid).task(row)


def test_outputs_are_frozen_absolute_and_checked_where_they_were_frozen(project, tmp_path):
    other = tmp_path / "other-repo"
    (other / "build").mkdir(parents=True)
    (other / "build/report.md").write_text("report")
    (project / "docs").mkdir()
    (project / "docs/a.md").write_text("a")
    _allow(project, other)
    fake(project, plan=plan(task("a", expected_outputs=[{"path": "docs/a.md", "description": "notes"},
                                                       str(other / "build/report.md"), str(other / "build") + "/"])),
         a=result())
    sid = chat.create_session(project)["id"]
    say(project, sid, "make it")
    state = run(project, sid)
    assert state["requests"][0]["status"] == "done"
    outputs = _rec(project, sid, "a")["outputs"]
    assert [(o["id"], o["path"], o["folder"], o["rev"], o["history"]) for o in outputs] == [
        ("o1", str((project / "docs/a.md").resolve()), False, 1, []),
        ("o2", str((other / "build/report.md").resolve()), False, 1, []),
        ("o3", str((other / "build").resolve()), True, 1, [])]
    assert outputs[0]["description"] == "notes" and outputs[0]["written"] == "docs/a.md"
    prompt = next(c["prompt"] for c in calls(project) if c["task"] == "a")
    assert f"- o1: `{(project / 'docs/a.md').resolve()}` — notes" in prompt
    assert f"- o3: `{(other / 'build').resolve()}/`" in prompt
    planner = next(c["prompt"] for c in calls(project) if c["kind"] == "plan")
    assert "Give each expected output as an absolute path" in planner and "ALFRD fixes these paths" in planner


def test_the_check_uses_the_frozen_path_not_the_plan_text(project):
    (project / "frozen.txt").write_text("x")
    fake(project, plan=plan(task("a", expected_outputs=["frozen.txt"])), a=result())
    sid = chat.create_session(project)["id"]
    say(project, sid, "make it")
    run(project, sid)
    store = chat.Session(project, sid)
    rec = _rec(project, sid, "a")
    assert chat.missing_task_outputs(chat.task_outputs(store, rec)) == []
    rec["task"]["expected_outputs"] = ["elsewhere.txt"]   # the plan text no longer matters
    assert chat.missing_task_outputs(chat.task_outputs(store, rec)) == []
    rec["outputs"][0]["path"] = str(project / "moved.txt")
    assert [o["id"] for o in chat.missing_task_outputs(chat.task_outputs(store, rec))] == ["o1"]
    legacy = {k: v for k, v in rec.items() if k != "outputs"}   # made before outputs were frozen
    assert chat.task_outputs(store, legacy)[0]["path"] == str((project / "elsewhere.txt").resolve())


def test_an_output_outside_the_writable_folders_is_a_plan_error(project, tmp_path):
    outside = tmp_path / "nowhere/out.txt"
    fake(project, plan=plan(task("a", expected_outputs=[str(outside)])),
         repair=plan(task("a", expected_outputs=["out.txt"])), a=result())
    (project / "out.txt").write_text("ok")
    sid = chat.create_session(project)["id"]
    say(project, sid, "make it")
    state = run(project, sid)
    assert [c["kind"] for c in orchestrator_calls(project)] == ["plan", "repair"]
    repair = next(c["prompt"] for c in calls(project) if c["kind"] == "repair")
    assert f"expected output {str(outside)!r} resolves to {outside}" in repair
    assert "outside the project" in repair
    assert state["requests"][0]["status"] == "done"
    assert len([c for c in calls(project) if c["task"] == "a"]) == 1   # the refused plan never ran


def test_a_relative_path_escaping_the_project_is_refused():
    errors = chat.output_errors("/proj", {"id": "a", "expected_outputs": ["../escape.txt", "ok.txt"]}, [])
    assert len(errors) == 1 and "'../escape.txt'" in errors[0]
    assert chat.output_errors("/proj", {"id": "a", "expected_outputs": ["/data/x/y.txt"]}, ["/data/x"]) == []


def test_delegated_subtasks_with_unwritable_outputs_are_an_invalid_result(project, tmp_path):
    fake(project, plan=plan(task("a")),
         a=[result(tasks=[task("sub", expected_outputs=[str(tmp_path / "nowhere.txt")])]), result()])
    sid = chat.create_session(project)["id"]
    say(project, sid, "make it")
    state = run(project, sid)
    a = next(t for t in state["tasks"] if t["row"].endswith(".a"))
    assert a["attempts"] == 2 and not [t for t in state["tasks"] if t["row"].endswith(".sub")]


# B2: an agent reports a changed output (result "outputs_changed")


def test_an_agent_reports_a_moved_output_and_dependents_get_the_new_path(project, tmp_path):
    other = tmp_path / "other-repo"
    other.mkdir()
    (other / "report.md").write_text("moved here")
    _allow(project, other)
    fake(project, plan=plan(task("a", expected_outputs=[{"path": "report.md", "description": "the report"}]),
                            task("b", owner="sonnet", deps=["a"])),
         a=result(outputs_changed=[{"output": "o1", "to": str(other / "report.md"), "reason": "lives with the code"}]),
         b=result())
    sid = chat.create_session(project)["id"]
    say(project, sid, "make it")
    state = run(project, sid)
    assert state["requests"][0]["status"] == "done"                      # checked at the new path, not report.md
    [out] = _rec(project, sid, "a")["outputs"]
    assert out["path"] == str((other / "report.md").resolve()) and out["rev"] == 2
    assert out["history"][0]["path"] == str((project / "report.md").resolve())
    assert out["history"][0]["reason"] == "lives with the code" and out["history"][0]["by"] == "codex"
    first = next(c["prompt"] for c in calls(project) if c["task"] == "a")
    assert '"outputs_changed"' in first
    dependent = next(c["prompt"] for c in calls(project) if c["task"] == "b")
    assert f"output o1: `{(other / 'report.md').resolve()}` (rev 2, moved)" in dependent
    assert any(m.get("kind") == "output_changed" and m.get("output") == "o1" for m in state["messages"])
    moved = [e for e in events(project, sid) if e.get("kind") == "task.output_changed"]
    assert moved and moved[0]["data"]["to"] == str((other / "report.md").resolve())


def test_a_move_outside_the_writable_folders_is_refused_and_retried(project, tmp_path):
    (project / "report.md").write_text("here")
    configure(project, bounds={"max_retries": 2})
    fake(project, plan=plan(task("a", expected_outputs=["report.md"], budget={"max_retries": 2})),
         a=[result(outputs_changed=[{"output": "o1", "to": str(tmp_path / "x.md")}]),
            result(outputs_changed=[{"output": "o9", "to": str(project / "y.md")}]),
            result()])
    sid = chat.create_session(project)["id"]
    say(project, sid, "make it")
    state = run(project, sid)
    a = next(t for t in state["tasks"] if t["row"].endswith(".a"))
    assert a["status"] == "done" and a["attempts"] == 3
    [out] = _rec(project, sid, "a")["outputs"]
    assert out["rev"] == 1 and out["history"] == []
    prompts = [c["prompt"] for c in calls(project) if c["task"] == "a"]
    assert "outside the project and this task's writable folders" in prompts[1]
    assert "o9: no such expected output (this task has o1)" in prompts[2]


def test_outputs_changed_must_be_well_formed():
    with pytest.raises(proto.ProtocolError):
        proto.validate("result", {"status": "done", "summary": "x", "outputs_changed": [{"output": "x1", "to": "/a"}]})
    proto.validate("result", {"status": "done", "summary": "x", "outputs_changed": [{"output": "o1", "to": "/a"}]})


def test_a_writable_root_itself_is_not_an_output():
    # A root always exists, so an output "at" it would pass the missing-output check without being written.
    assert chat.output_errors("/proj", {"id": "a", "expected_outputs": ["."]}, [])
    assert chat.output_errors("/proj", {"id": "a", "expected_outputs": ["/data/x"]}, ["/data/x"])
    assert chat.output_errors("/proj", {"id": "a", "expected_outputs": ["/data/x/out/"]}, ["/data/x"]) == []


def test_a_move_onto_a_root_or_the_same_output_twice_is_refused(project):
    (project / "report.md").write_text("here")
    configure(project, bounds={"max_retries": 2})
    fake(project, plan=plan(task("a", expected_outputs=["report.md"], budget={"max_retries": 2})),
         a=[result(outputs_changed=[{"output": "o1", "to": str(project)}]),
            result(outputs_changed=[{"output": "o1", "to": str(project / "x.md")},
                                    {"output": "o1", "to": str(project / "report.md")}]),
            result()])
    sid = chat.create_session(project)["id"]
    say(project, sid, "make it")
    state = run(project, sid)
    a = next(t for t in state["tasks"] if t["row"].endswith(".a"))
    assert a["status"] == "done" and a["attempts"] == 3
    [out] = _rec(project, sid, "a")["outputs"]
    assert out["rev"] == 1 and out["history"] == []          # all or none: the valid first change was not applied
    prompts = [c["prompt"] for c in calls(project) if c["task"] == "a"]
    assert "(or is one of those folders itself)" in prompts[1]
    assert "o1: changed twice in one result" in prompts[2]


# B3: the moved path reaches the orchestrator's prompts, the code summary, promoted steps and the Files tab


def test_a_moved_output_reaches_prompts_summary_graph_and_files(project):
    (project / "sub").mkdir()
    (project / "sub/report.md").write_text("moved here")
    fake(project, plan=plan(task("a", expected_outputs=["report.md"])),
         a=result(outputs_changed=[{"output": "o1", "to": str(project / "sub/report.md"), "reason": "tidier"}]))
    sid = chat.create_session(project)["id"]
    say(project, sid, "make it")
    state = run(project, sid)
    assert state["requests"][0]["status"] == "done"
    store = chat.Session(project, sid)
    rid = state["requests"][0]["id"]
    req, rec = store.request(rid), _rec(project, sid, "a")
    new, old = (project / "sub/report.md").resolve(), (project / "report.md").resolve()
    moved = f"o1: `{old}` → `{new}` (rev 2, by @codex): tidier"
    assert "## Outputs moved in this request" in chat.summary_prompt(store, req)
    assert f"output o1: `{new}` (rev 2, moved)" in chat.summary_prompt(store, req)
    assert moved in chat.replan_prompt(store, chat.settings(project), rec, [])
    summary = chat._code_summary(store, req)
    assert "`sub/report.md`" in summary and moved in summary
    files = {f["path"]: f["state"] for f in chat.overview(project, sid)["files"]}
    assert files.get("sub/report.md") == "recorded" and "report.md" not in files
    preview = chat.promotion(project, sid, rid)
    assert "sub/report.md" in preview["text"] and "- report.md" not in preview["text"]


def test_legacy_records_show_their_planned_outputs():
    rec = {"row": "r1.a", "task": {"expected_outputs": [{"path": "out/", "description": "d"}, "x.txt"]}}
    outputs = chat.outputs_of("/proj", rec)
    assert [chat.output_display_path("/proj", o) for o in outputs] == ["out/", "x.txt"]
    assert chat.output_display_path("/proj", {"path": "/data/y.txt"}) == "/data/y.txt"


# B2: an agent reports a moved output by mail (code OUT)


def out_mail(output, to, key, **payload):
    return {"recipient": {"kind": "orchestrator"}, "kind": "info", "code": "OUT", "subject": f"{output} moved",
            "body": "moved", "idempotency_key": key, "payload": {"output": output, "to": to, **payload}}


def test_out_mail_is_coordination_and_must_name_output_and_path(box):
    assert (CODES["OUT"]["kind"], CODES["OUT"]["category"]) == ("info", "coordination")
    sent = box.send(AGENT, out_mail("o1", "/abs/x.md", "k1", reason="why"))["message"]
    assert (sent["code"], sent["category"], sent["priority"]) == ("OUT", "coordination", "high")
    for bad in ({"output": "x1", "to": "/a"}, {"output": "o1"}):
        with pytest.raises(MailboxError, match="payload"):
            box.send(AGENT, {**out_mail("o1", "/a", "k2"), "payload": bad})


def test_this_attempts_out_mail_joins_the_result_changes(box):
    store = box.session
    rec = store.task("r0001.a")
    rec["units"] = ["0001-r0001.a-codex", AGENT["unit_id"]]
    store.save_task(rec)
    box.send({**AGENT, "unit_id": "0001-r0001.a-codex"}, out_mail("o3", "/earlier", "k0"))  # earlier attempt: ignored
    box.send(AGENT, out_mail("o1", "/first", "k1"))
    last = box.send(AGENT, out_mail("o1", "/second", "k2", reason="r"))["message"]
    box.send(AGENT, out_mail("o2", "/by/mail", "k3"))
    changes = chat._with_out_mail(store, store.request("r0001"), rec, {"id": AGENT["unit_id"]},
                                  [{"output": "o2", "to": "/by/result"}])
    assert changes == [{"output": "o2", "to": "/by/result"},                             # the result wins over mail
                       {"output": "o1", "to": "/second", "reason": "r", "mail": last["id"]}]  # the last mail wins


class _Runner:
    def __init__(self, sid):
        self.plan, self.events = {"chat": {"session": sid}}, []

    def event(self, kind, text=None, **data):
        self.events.append((kind, data))


def test_a_mailed_move_is_applied_and_linked_to_its_mail(project):
    (project / "report.md").write_text("here")
    (project / "sub").mkdir()
    fake(project, plan=plan(task("a", expected_outputs=["report.md"])), a=result())
    sid = chat.create_session(project)["id"]
    say(project, sid, "make it")
    state = run(project, sid)
    store, rec = chat.Session(project, sid), _rec(project, sid, "a")
    runner = _Runner(sid)
    changes = [{"output": "o1", "to": str(project / "sub/report.md"), "reason": "tidier", "mail": "msg_x"}]
    assert chat._change_outputs(runner, store, store.request(state["requests"][0]["id"]), rec,
                                {"id": rec["units"][-1]}, changes) == []
    [out] = store.task(rec["row"])["outputs"]
    assert out["rev"] == 2 and out["history"][0]["mailbox_message_id"] == "msg_x"
    assert runner.events == [("task.output_changed", runner.events[0][1])]
    assert runner.events[0][1]["mailbox_message_id"] == "msg_x"


def test_two_outputs_cannot_share_a_path_and_a_move_must_change_it(project):
    # One file must not pass the missing-output check for two outputs, and a "move" in place is no move.
    for name in ("a.md", "b.md"):
        (project / name).write_text(name)
    fake(project, plan=plan(task("a", expected_outputs=["a.md", "b.md"])), a=result())
    sid = chat.create_session(project)["id"]
    say(project, sid, "make it")
    state = run(project, sid)
    store, rec = chat.Session(project, sid), _rec(project, sid, "a")
    req, unit, runner = store.request(state["requests"][0]["id"]), {"id": rec["units"][-1]}, _Runner(sid)
    a, b, c = (str((project / n).resolve()) for n in ("a.md", "b.md", "c.md"))
    assert chat._change_outputs(runner, store, req, rec, unit, [{"output": "o1", "to": b}]) == [
        f"o1: {b} would also be the path of o2"]
    assert chat._change_outputs(runner, store, req, rec, unit, [{"output": "o1", "to": c}, {"output": "o2", "to": c}]) == [
        f"o1: {c} would also be the path of o2", f"o2: {c} would also be the path of o1"]
    assert chat._change_outputs(runner, store, req, rec, unit, [{"output": "o1", "to": a}]) == [f"o1: {a} is already its path"]
    assert runner.events == [] and all(o["rev"] == 1 for o in chat.task_outputs(store, store.task(rec["row"])))
    assert chat._change_outputs(runner, store, req, rec, unit, [{"output": "o1", "to": b}, {"output": "o2", "to": a}]) == []
    assert [o["path"] for o in store.task(rec["row"])["outputs"]] == [b, a]          # a swap is fine
