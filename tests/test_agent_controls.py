import json
import os
from pathlib import Path
import signal
import sys
import time

import pytest
import yaml

from test_agent_loop import RESPONSE, project, service, wait
from alfrd.agent_io import agent_command, ClaudeStream, codex_usage, agent_totals
from alfrd.agent_loop import approve_response, sha256
from alfrd.execution import load_execution
from alfrd.runtime import scheduler
from alfrd.api import status as api


def configure(project, *, review=False):
    path = project / "alfrd.yaml"
    data = yaml.safe_load(path.read_text())
    data["workflows"][0]["repeat"]["iterations"] = 2
    data["project_settings"] = {"human_review": review}
    data["workflows"][0]["turns"] = {2: {"human_review": False}}
    path.write_text(yaml.safe_dump(data))
    cfg = load_execution(project)
    from alfrd.runtime import plan_csv

    (project / "alfrd.plan.csv").unlink(missing_ok=True)
    plan_csv.create(project / "alfrd.plan.csv", [{"target": "task"}], cfg.step_ids, cfg.step_ids,
                    key_column=cfg.key_column, files_column=cfg.files_column,
                    code_column=cfg.code_column, workdir_column=cfg.workdir_column)
    return data


def test_handoff_results_expose_turn_timestamps(project, service, tmp_path):
    from alfrd.agent_loop import prepare
    from alfrd.gui import create_app
    from alfrd.gui.services import RuntimeCatalogReader

    folder = scheduler.create_plan(project)
    step = load_execution(project).steps[0]
    handoff = prepare(project, folder.path / "handoffs" / "result-turn", step, folder.id, "result-turn", target="task")
    folder.save_unit({"id": "result-turn", "handoff": handoff, "status": "done",
                      "iteration": 1, "agent": "claude", "started": "2026-10-03T10:00:00Z",
                      "finished": "2026-10-03T10:00:30Z", "row": "task", "steps": ["i001-claude-turn"]})
    app = create_app({"TESTING": True, "SECRET_KEY": "k", "RUNTIME_SERVICE": service,
                      "CATALOG_READER": RuntimeCatalogReader(service),
                      "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'results.sqlite'}"})
    identifier = service.list_projects()[0].identifier
    response = app.test_client().get(f"/api/studio/projects/{identifier}/plans/{folder.id}/handoffs")
    assert response.status_code == 200
    turn = response.get_json()["handoffs"][0]
    assert turn["started"] == "2026-10-03T10:00:00Z"
    assert turn["finished"] == "2026-10-03T10:00:30Z"
    assert turn["phase"] == "done"
    assert turn["row"] == "task"
    assert turn["steps"] == ["i001-claude-turn"]


def test_model_arguments_and_stream_preserve_other_flags():
    argv, model, streaming = agent_command(["claude", "-p", "--model", "old", "--add-dir", "/repo", "--output-format", "text"], "new", True)
    assert argv.count("--model") == 1 and model == "new"
    assert argv[argv.index("--add-dir") + 1] == "/repo"
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert "--verbose" in argv and streaming
    argv, model, _ = agent_command(["codex", "exec", "-m", "old", "-"], "new")
    assert argv[-3:] == ("--model", "new", "-")
    assert "-m" not in argv


def test_claude_result_records_tokens_and_cost(tmp_path):
    stream = ClaudeStream(tmp_path / "response.md", tmp_path / "unit.exit")
    usage = {"input_tokens": 120, "output_tokens": 35, "cache_read_input_tokens": 700, "cache_creation_input_tokens": 90}
    stream.feed(json.dumps({"type": "result", "subtype": "success", "result": RESPONSE,
                            "usage": usage, "total_cost_usd": 0.0123}) + "\n")
    assert stream.close() is None
    metadata = json.loads((tmp_path / "unit.agent.json").read_text())
    assert metadata["usage"] == {**usage, "total_tokens": 945, "input_uncached_tokens": 120}
    assert metadata["usage_source"] == "claude_result"
    assert metadata["total_cost_usd"] == 0.0123


def test_codex_reported_usage_and_unavailable_counts():
    assert codex_usage("model: codex\nresponse\ntokens used\n12,345\n")["total_tokens"] == 12345
    usage = codex_usage(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 15}}))
    assert usage["input_tokens"] == 100 and usage["total_tokens"] == 115
    assert codex_usage("plain response without a usage report") is None


def test_agent_totals_leave_unknown_metrics_null():
    totals = agent_totals([{"agent_usage": {"total_tokens": 5}, "total_cost_usd": 0.01},
                           {"agent_usage": None, "total_cost_usd": None},
                           {"agent_usage": {"total_tokens": 7}, "total_cost_usd": 0.02}])
    assert totals["total_tokens"] == 12
    assert totals["input_tokens"] is None
    assert totals["total_cost_usd"] == 0.03


def test_plan_token_total_includes_units_outside_page(project):
    folder = scheduler.create_plan(project)
    for index in range(3):
        folder.save_unit({"id": f"turn-{index}", "status": "done", "agent_usage": {"total_tokens": 10},
                          "total_cost_usd": 0.01, "steps": [], "handoff": {}})
    status = scheduler.plan_status(project, folder.id, units=1)
    assert len(status["units"]) == 1
    assert status["agent_totals"]["total_tokens"] == 30
    assert status["agent_totals"]["total_cost_usd"] == 0.03


@pytest.mark.parametrize("reported", [False, True])
def test_codex_completed_unit_persists_reported_or_unknown_usage(tmp_path, reported):
    from types import SimpleNamespace

    (tmp_path / "unit.exit").write_text('{"exit_code": 0}')
    (tmp_path / "unit.log").write_text("model: example-model\n" + ("tokens used\n1,234\n" if reported else "no usage report\n"))
    unit = {"id": "unit", "handoff": {}, "argv": ["codex", "exec"], "exit_file": "unit.exit", "log": "unit.log"}
    unit["handoff"] = {"roles": []}
    runner = scheduler.Runner.__new__(scheduler.Runner)
    runner.folder = SimpleNamespace(root=tmp_path, save_unit=lambda value: None)
    runner.agent_metadata(unit)
    metadata = json.loads((tmp_path / "unit.agent.json").read_text())
    assert metadata["usage"] == unit["agent_usage"]
    assert metadata["total_cost_usd"] is None
    if reported:
        assert unit["agent_usage"]["total_tokens"] == 1234
    else:
        assert unit["agent_usage"] is None


@pytest.mark.parametrize("failure", [True, False])
def test_claude_stream_keeps_activity_out_of_handoff(tmp_path, capsys, failure):
    response = tmp_path / "response.md"
    stream = ClaudeStream(response, tmp_path / "unit.exit")
    events = [
        {"type": "system", "subtype": "init", "model": "claude-reported"},
        {"type": "assistant", "message": {"model": "claude-reported", "content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "app.py"}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "file contents"}]}},
        {"type": "stream_event", "event": {"delta": {"type": "text_delta", "text": "Working..."}}},
        {"type": "result", "subtype": "error_during_execution" if failure else "success", "is_error": failure, "result": RESPONSE,
         "usage": {"input_tokens": 123, "output_tokens": 45}, "total_cost_usd": 0.02},
    ]
    for event in events:
        stream.feed(json.dumps(event) + "\n")
    assert bool(stream.close()) == failure
    assert response.exists() != failure
    if not failure:
        assert response.read_text() == RESPONSE
    log = capsys.readouterr().out
    assert "Read" in log and "file contents" in log and "Working..." in log
    assert json.loads((tmp_path / "unit.agent.json").read_text())["model"] == "claude-reported"


def test_claude_status_note_after_the_report_does_not_replace_the_handoff(tmp_path, capsys):
    """Seen 2026-10-08: a stopped background waiter got a 284-character reply after the report."""
    from alfrd.agent_loop import REQUIRED_HEADINGS

    response = tmp_path / "response.md"
    stream = ClaudeStream(response, tmp_path / "unit.exit", headings=list(REQUIRED_HEADINGS))
    for text in ("Still in progress. Waiting for the notification.", RESPONSE, "The closing report above stands."):
        stream.feed(json.dumps({"type": "result", "subtype": "success", "result": text}) + "\n")
    assert stream.close() is None
    assert response.read_text() == RESPONSE  # the early note was replaced; the late one was not
    assert "Kept the earlier handoff" in capsys.readouterr().out
    later = RESPONSE.replace("## Goal", "## Goal\nRevised.", 1)
    stream = ClaudeStream(response, tmp_path / "unit.exit", headings=list(REQUIRED_HEADINGS))
    for text in (RESPONSE, later):
        stream.feed(json.dumps({"type": "result", "subtype": "success", "result": text}) + "\n")
    assert response.read_text() == later  # the last valid handoff wins


def test_human_adjustment_blocks_next_agent_and_survives_runner_death(project):
    configure(project, review=True)
    original = (project / "task/next-step-codex.md").read_text()
    folder = scheduler.create_plan(project)
    scheduler.spawn_runner(folder)
    try:
        assert wait(lambda: folder.units() and folder.units()[0].get("review_status") == "pending")
        unit = folder.units()[0]
        assert api.plan_status(project, folder.id)["loop"]["phase"] == "awaiting_review"
        assert len((project / "calls.jsonl").read_text().splitlines()) == 1
        assert (project / "task/next-step-codex.md").read_text() == original
        response = Path(unit["handoff"]["response_file"])
        before = response.read_text()
        with pytest.raises(Exception, match="changed on disk"):
            approve_response(project, folder.id, unit["id"], RESPONSE, "stale")
        os.kill(folder.load()["runner"]["pid"], signal.SIGKILL)
        assert wait(lambda: not folder.runner_alive())
        edited = before + "\nHuman: focus on accessibility.\n"
        approve_response(project, folder.id, unit["id"], edited, sha256(before))
        scheduler.reconcile(project)
        assert wait(lambda: folder.load()["status"] == "finished")
        calls = [json.loads(line) for line in (project / "calls.jsonl").read_text().splitlines()]
        assert len(calls) == 2 and "Human: focus on accessibility." in calls[1]["prompt"]
        assert response.with_name("response-agent.md").read_text() == before
    finally:
        if folder.load()["status"] == "running":
            scheduler.control(project, folder.id, "cancel")


def test_cancel_pending_human_review_preserves_handoff(project):
    configure(project, review=True)
    original = (project / "task/next-step-codex.md").read_bytes()
    folder = scheduler.create_plan(project)
    scheduler.spawn_runner(folder)
    assert wait(lambda: folder.units() and folder.units()[0].get("review_status") == "pending")
    scheduler.control(project, folder.id, "cancel")
    assert wait(lambda: folder.load()["status"] == "cancelled")
    assert (project / "task/next-step-codex.md").read_bytes() == original
    assert len(folder.units()) == 1


def test_review_wait_respects_total_runtime_budget(project):
    configure(project, review=True)
    path = project / "alfrd.yaml"
    data = yaml.safe_load(path.read_text())
    data["execution"].update(max_runtime=3, kill_grace=.2)
    path.write_text(yaml.safe_dump(data))
    original = (project / "task/next-step-codex.md").read_bytes()
    folder = scheduler.create_plan(project)
    scheduler.spawn_runner(folder)
    assert wait(lambda: folder.units() and folder.units()[0].get("review_status") == "pending")
    assert wait(lambda: folder.load()["status"] == "failed")
    assert folder.load()["error"] == "total runtime limit reached"
    assert (project / "task/next-step-codex.md").read_bytes() == original


@pytest.mark.parametrize("failure", [False, True])
def test_real_shim_stream_reports_model_and_publishes_only_result(project, tmp_path, failure):
    configure(project)
    executable = tmp_path / "claude"
    events = [
        {"type": "system", "subtype": "init", "model": "resolved-model"},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "task.md"}}]}},
        {"type": "result", "subtype": "error_during_execution" if failure else "success", "is_error": failure, "result": RESPONSE,
         "usage": {"input_tokens": 123, "output_tokens": 45}, "total_cost_usd": 0.02},
    ]
    executable.write_text(f"#!{sys.executable}\nimport json,sys\nsys.stdin.read()\nfor event in {events!r}: print(json.dumps(event), flush=True)\n")
    executable.chmod(0o755)
    path = project / "alfrd.yaml"
    data = yaml.safe_load(path.read_text())
    data["entrypoint"][0].update(cmd=[str(executable), "-p", "--output-format", "text"], model="requested-model")
    path.write_text(yaml.safe_dump(data))
    folder = scheduler.create_plan(project)
    scheduler.Runner(project, folder.id).run()
    unit = folder.units()[0]
    assert unit["model"] == "resolved-model" and unit["requested_model"] == "requested-model"
    assert unit["agent_usage"]["total_tokens"] == 168
    assert unit["total_cost_usd"] == 0.02
    if failure:
        assert not Path(unit["handoff"]["response_file"]).exists()
        assert len(folder.units()) == 1
    else:
        assert Path(unit["handoff"]["response_file"]).read_text() == RESPONSE
    assert "Tool: Read" in (project / unit["log"]).read_text()
    assert folder.load()["status"] == ("failed" if failure else "finished")


def test_claude_that_lingers_after_its_final_result_is_stopped_and_succeeds(project, tmp_path, monkeypatch):
    """claude -p can stay open after background agents report; the turn must still end."""
    configure(project)
    monkeypatch.setenv("ALFRD_CLAUDE_EXIT_GRACE", "1")
    executable = tmp_path / "claude"
    events = [
        {"type": "system", "subtype": "init", "model": "resolved-model"},
        {"type": "system", "subtype": "background_tasks_changed", "tasks": [{"task_id": "a1"}]},
        {"type": "result", "subtype": "success", "result": "Waiting for the agent."},
        {"type": "system", "subtype": "background_tasks_changed", "tasks": []},
        {"type": "result", "subtype": "success", "result": RESPONSE, "usage": {"input_tokens": 1, "output_tokens": 2}},
    ]
    executable.write_text(f"#!{sys.executable}\nimport json,sys,time\nsys.stdin.read()\n"
                          f"for event in {events!r}: print(json.dumps(event), flush=True)\ntime.sleep(600)\n")
    executable.chmod(0o755)
    path = project / "alfrd.yaml"
    data = yaml.safe_load(path.read_text())
    data["entrypoint"][0].update(cmd=[str(executable), "-p", "--output-format", "text"])
    path.write_text(yaml.safe_dump(data))
    folder = scheduler.create_plan(project)
    started = time.time()
    scheduler.Runner(project, folder.id).run()
    assert time.time() - started < 60
    unit = folder.units()[0]
    assert Path(unit["handoff"]["response_file"]).read_text() == RESPONSE
    assert "stayed open" in (project / unit["log"]).read_text()
    assert folder.load()["status"] == "finished"


def test_task_editor_conflicts_sync_and_csrf(project, tmp_path, service):
    from alfrd.gui import create_app
    from alfrd.gui.services import RuntimeCatalogReader

    app = create_app({"TESTING": True, "SECRET_KEY": "k", "RUNTIME_SERVICE": service,
                      "CATALOG_READER": RuntimeCatalogReader(service),
                      "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'catalog.sqlite'}", "STUDIO_PROJECTS": [str(project)]})
    client = app.test_client()
    headers = {"X-CSRF-Token": client.get("/api/studio/session").get_json()["csrf_token"]}
    # Resolve the same location identifier the API uses.
    identifier = service.list_projects()[0].identifier
    prefix = f"/api/studio/projects/{identifier}"
    current = client.get(prefix + "/task").get_json()
    payload = {"text": "Build the new feature", "base_hash": current["hash"], "use_for_next_run": True}
    assert client.post(prefix + "/task", json=payload).status_code == 403
    response = client.post(prefix + "/task", json=payload, headers=headers)
    assert response.status_code == 200, response.get_json()
    assert (project / "task/next-step-claude.md").read_text() == "Build the new feature\n"
    assert client.post(prefix + "/task", json=payload, headers=headers).status_code == 409
    configure(project, review=True)
    folder = scheduler.create_plan(project)
    scheduler.spawn_runner(folder)
    assert wait(lambda: folder.units() and folder.units()[0].get("review_status") == "pending")
    latest = client.get(prefix + "/task").get_json()
    response = client.post(prefix + "/task", json={"text": "another task", "base_hash": latest["hash"], "use_for_next_run": True}, headers=headers)
    assert response.status_code == 409
    assert (project / "task/task.md").read_text() == latest["text"]
    handoffs = client.get(prefix + f"/plans/{folder.id}/handoffs").get_json()["handoffs"]
    unit = handoffs[0]
    content = client.get(prefix + f"/plans/{folder.id}/handoffs/{unit['id']}/response").get_json()["content"]
    review = {"unit": unit["id"], "text": content + "\nReviewed by a human", "base_hash": sha256(content)}
    endpoint = prefix + f"/plans/{folder.id}/review"
    assert client.post(endpoint, json=review).status_code == 403
    assert client.post(endpoint, json=review, headers=headers).status_code == 200
    assert client.post(endpoint, json=review, headers=headers).status_code == 409
    assert wait(lambda: folder.load()["status"] == "finished")
