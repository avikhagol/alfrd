"""Attempt → task → request telemetry rollup with coverage (U4): ``telemetry.totals`` and the chat payloads."""
from __future__ import annotations

import json

from alfrd import chat, telemetry
from tests.test_chat_orchestration import project, say  # noqa: F401  (the project fixture)


def use(uid, name="Read", command=None):
    args = {"command": command} if command is not None else {}
    return json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": uid, "name": name, "input": args}]}}) + "\n"


def failed(uid):
    return json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": uid, "is_error": True}]}}) + "\n"


def events(tmp_path, name, lines):
    path = tmp_path / f"{name}.events.jsonl"
    path.write_text("".join(lines))
    return path


def doc_of(path, *, live=False):
    """An attempt's sidecar document as the views build it (a running one is marked by ``telemetry.running``)."""
    doc = telemetry.advance(path, "claude", finalize=not live, write=False)
    return telemetry.running(doc) if live else doc


# ---------------------------------------------------------------- pure tally


def test_complete_zero_is_zero(tmp_path):
    got = telemetry.totals([doc_of(events(tmp_path, "empty", []))])
    assert got["attempts"] == 1
    assert got["counts"]["tool_calls"] == {"value": 0, "coverage": "complete"}


def test_failed_attempts_count_once_each(tmp_path):
    first = events(tmp_path, "a", [use("t1", "Bash", "ls"), failed("t1")])
    # The same tool id in another attempt is a different call: dedupe never crosses attempts.
    second = events(tmp_path, "b", [use("t1", "Edit"), failed("t1")])
    counts = telemetry.totals([doc_of(first), doc_of(second)])["counts"]
    assert counts["tool_calls"] == {"value": 2, "coverage": "complete"}
    assert counts["tool_errors"] == {"value": 2, "coverage": "complete"}
    assert counts["shell"]["value"] == 1 and counts["file_edit"]["value"] == 1


def test_running_attempt_gives_a_lower_bound(tmp_path):
    done = events(tmp_path, "done", [use("a"), use("b")])
    running = events(tmp_path, "run", [use("c")])
    got = telemetry.totals([doc_of(done), doc_of(running, live=True)])
    assert got["counts"]["tool_calls"] == {"value": 3, "coverage": "partial"}  # At least 3


def test_partial_zero_stays_null(tmp_path):
    running = events(tmp_path, "run", [])
    got = telemetry.totals([doc_of(running, live=True)])
    assert got["counts"]["tool_calls"] == {"value": None, "coverage": "partial"}


def test_missing_sidecar_makes_partial_not_zero(tmp_path):
    done = events(tmp_path, "done", [use("a")])
    got = telemetry.totals([doc_of(done), None])
    assert got["counts"]["tool_calls"] == {"value": 1, "coverage": "partial"}


def test_no_data_is_null(tmp_path):
    assert telemetry.totals([None])["counts"]["tool_calls"] == {"value": None, "coverage": "unavailable"}
    assert telemetry.totals([])["counts"]["tool_calls"] == {"value": None, "coverage": "unavailable"}


def test_counter_the_adapter_cannot_report_is_unavailable(tmp_path):
    path = tmp_path / "c.events.jsonl"
    path.write_text(json.dumps({"type": "item.completed", "item": {"id": "x", "type": "command_execution",
                                                                  "command": "ls"}}) + "\n")
    counts = telemetry.totals([telemetry.advance(path, "codex", finalize=True, write=False)])["counts"]
    assert counts["file_read"] == {"value": None, "coverage": "unavailable"}
    assert counts["shell"] == {"value": 1, "coverage": "complete"}


# ---------------------------------------------------------------- chat payloads


def make_session(project, name):
    sid = chat.create_session(project, name)["id"]
    say(project, sid, "@codex build the report")
    store = chat.session(project, sid)
    return sid, store, store.requests()[0]


def orchestrator_row(store, req):
    """An orchestrator row (kind plan) of the request, appended the way the planner records one."""
    row = f"{req['id']}.plan"
    store.save_task({"row": row, "request": req["id"], "kind": "plan", "owner": "claude", "status": "done",
                     "created_ns": 0, "result": {"status": "done", "summary": "ok", "artifacts": []}})
    store.save_request({**req, "rows": [*req["rows"], row]})
    return row


def add_unit(store, uid, row, status, lines=None, *, log=True):
    folder = chat._plan_folder(store)
    unit = {"id": uid, "row": row, "status": status, "adapter": "claude", "attempt_number": 1}
    if log:
        unit["log"] = str((folder.logs_dir / f"{uid}.log").relative_to(store.root))
        if lines is not None:
            folder.logs_dir.mkdir(parents=True, exist_ok=True)
            (folder.logs_dir / f"{uid}.events.jsonl").write_text("".join(lines))
    folder.save_unit(unit)


def test_attempts_roll_up_to_task_and_request_with_coverage(project):
    sid, store, req = make_session(project, "Rollup")
    task_row = req["rows"][0]
    plan_row = orchestrator_row(store, req)
    add_unit(store, "u1", task_row, "done", [use("a", "Bash", "ls"), use("b"), failed("b")])
    add_unit(store, "u2", task_row, "failed", [use("c", "Edit"), failed("c")])
    add_unit(store, "u3", plan_row, "done", [use("d", "Bash", "rtk alfrd mailbox send --to user")])
    add_unit(store, "u4", task_row, "running", [use("e", "WebSearch")])

    state = chat.session_state(project, sid)
    request = state["requests"][0]["telemetry"]
    assert request["attempts"] == 4  # the failed and the orchestrator attempts count, once each
    assert request["counts"]["tool_calls"] == {"value": 5, "coverage": "partial"}  # u4 still runs: at least 5
    assert request["counts"]["tool_errors"] == {"value": 2, "coverage": "partial"}
    assert request["counts"]["mailbox"] == {"value": 1, "coverage": "partial"}
    assert request["counts"]["turns"] == {"value": None, "coverage": "partial"}  # no turn seen, so no 0
    tasks = {t["row"]: t["telemetry"] for t in state["tasks"]}
    assert tasks[task_row]["attempts"] == 3
    assert tasks[task_row]["counts"]["tool_calls"] == {"value": 4, "coverage": "partial"}
    assert tasks[plan_row]["counts"]["tool_calls"] == {"value": 1, "coverage": "complete"}
    assert tasks[plan_row]["counts"]["mailbox"] == {"value": 1, "coverage": "complete"}
    assert state["telemetry"]["attempts"] == 4

    activity = chat.activity(project, sid)
    assert [item["unit"] for item in activity["items"]] == ["u4"]
    assert activity["items"][0]["telemetry"]["coverage"] == "partial"  # a running item is never complete
    assert activity["telemetry"]["tasks"][task_row]["attempts"] == 3
    assert activity["telemetry"]["requests"][req["id"]]["counts"]["tool_calls"] == {"value": 5, "coverage": "partial"}
    scoped = chat.activity(project, sid, request_id=req["id"])
    assert scoped["telemetry"]["requests"][req["id"]]["attempts"] == 4


def test_record_without_log_loads_as_null(project):
    sid, store, req = make_session(project, "NoLog")
    add_unit(store, "old", req["rows"][0], "done", log=False)  # written before logs existed: nothing to count
    state = chat.session_state(project, sid)
    assert state["requests"][0]["telemetry"]["counts"]["tool_calls"] == {"value": None, "coverage": "unavailable"}
    assert state["tasks"][0]["telemetry"]["attempts"] == 1


def test_log_without_sidecar_is_built_and_finished_zero_is_complete(project):
    sid, store, req = make_session(project, "Zero")
    row = req["rows"][0]
    add_unit(store, "plain", row, "done", [use("a"), use("b")])  # no sidecar yet: the bounded advance builds it
    state = chat.session_state(project, sid)
    assert state["requests"][0]["telemetry"]["counts"]["tool_calls"] == {"value": 2, "coverage": "complete"}
    assert telemetry.sidecar_path(chat._plan_folder(store).logs_dir / "plain.events.jsonl").exists()

    sid2, store2, req2 = make_session(project, "Zero2")
    add_unit(store2, "empty", req2["rows"][0], "done", [])  # finished, no calls: a real, complete zero
    counts = chat.session_state(project, sid2)["requests"][0]["telemetry"]["counts"]
    assert counts["tool_calls"] == {"value": 0, "coverage": "complete"}
