import json
import os
from pathlib import Path
import signal
import sys

import pytest
import yaml

from test_agent_loop import RESPONSE, project, service, wait
from alfrd.agent_io import agent_command, ClaudeStream
from alfrd.agent_loop import approve_response, sha256
from alfrd.execution import load_execution
from alfrd.runtime import scheduler
from alfrd.api import status as api


def configure(project, *, review=False):
    path = project / "alfrd.yaml"
    data = yaml.safe_load(path.read_text())
    data["workflows"][0]["repeat"]["iterations"] = 1
    data["project_settings"] = {"human_review": review}
    data["workflows"][0]["steps"][1]["human_review"] = False
    path.write_text(yaml.safe_dump(data))
    cfg = load_execution(project)
    from alfrd.runtime import plan_csv

    plan_csv.create(project / "alfrd.plan.csv", [{"target": "task"}], cfg.step_ids, cfg.step_ids,
                    key_column=cfg.key_column, files_column=cfg.files_column,
                    code_column=cfg.code_column, workdir_column=cfg.workdir_column)
    return data


def test_model_arguments_and_stream_preserve_other_flags():
    argv, model, streaming = agent_command(["claude", "-p", "--model", "old", "--add-dir", "/repo", "--output-format", "text"], "new", True)
    assert argv.count("--model") == 1 and model == "new"
    assert argv[argv.index("--add-dir") + 1] == "/repo"
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert "--verbose" in argv and streaming
    argv, model, _ = agent_command(["codex", "exec", "-m", "old", "-"], "new")
    assert argv[-3:] == ("--model", "new", "-")
    assert "-m" not in argv


@pytest.mark.parametrize("failure", [True, False])
def test_claude_stream_keeps_activity_out_of_handoff(tmp_path, capsys, failure):
    response = tmp_path / "response.md"
    stream = ClaudeStream(response, tmp_path / "unit.exit")
    events = [
        {"type": "system", "subtype": "init", "model": "claude-reported"},
        {"type": "assistant", "message": {"model": "claude-reported", "content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "app.py"}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "file contents"}]}},
        {"type": "stream_event", "event": {"delta": {"type": "text_delta", "text": "Working..."}}},
        {"type": "result", "subtype": "error_during_execution" if failure else "success", "is_error": failure, "result": RESPONSE},
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


def test_human_adjustment_blocks_next_agent_and_survives_runner_death(project):
    configure(project, review=True)
    original = (project / "next-step-codex.md").read_text()
    folder = scheduler.create_plan(project)
    scheduler.spawn_runner(folder)
    try:
        assert wait(lambda: folder.units() and folder.units()[0].get("review_status") == "pending")
        unit = folder.units()[0]
        assert api.plan_status(project, folder.id)["loop"]["phase"] == "awaiting_review"
        assert len((project / "calls.jsonl").read_text().splitlines()) == 1
        assert (project / "next-step-codex.md").read_text() == original
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
    original = (project / "next-step-codex.md").read_bytes()
    folder = scheduler.create_plan(project)
    scheduler.spawn_runner(folder)
    assert wait(lambda: folder.units() and folder.units()[0].get("review_status") == "pending")
    scheduler.control(project, folder.id, "cancel")
    assert wait(lambda: folder.load()["status"] == "cancelled")
    assert (project / "next-step-codex.md").read_bytes() == original
    assert len(folder.units()) == 1


def test_review_wait_respects_total_runtime_budget(project):
    configure(project, review=True)
    path = project / "alfrd.yaml"
    data = yaml.safe_load(path.read_text())
    data["execution"].update(max_runtime=3, kill_grace=.2)
    path.write_text(yaml.safe_dump(data))
    original = (project / "next-step-codex.md").read_bytes()
    folder = scheduler.create_plan(project)
    scheduler.spawn_runner(folder)
    assert wait(lambda: folder.units() and folder.units()[0].get("review_status") == "pending")
    assert wait(lambda: folder.load()["status"] == "failed")
    assert folder.load()["error"] == "total runtime limit reached"
    assert (project / "next-step-codex.md").read_bytes() == original


@pytest.mark.parametrize("failure", [False, True])
def test_real_shim_stream_reports_model_and_publishes_only_result(project, tmp_path, failure):
    configure(project)
    executable = tmp_path / "claude"
    events = [
        {"type": "system", "subtype": "init", "model": "resolved-model"},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "task.md"}}]}},
        {"type": "result", "subtype": "error_during_execution" if failure else "success", "is_error": failure, "result": RESPONSE},
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
    if failure:
        assert not Path(unit["handoff"]["response_file"]).exists()
        assert len(folder.units()) == 1
    else:
        assert Path(unit["handoff"]["response_file"]).read_text() == RESPONSE
    assert "Tool: Read" in (project / unit["log"]).read_text()
    assert folder.load()["status"] == ("failed" if failure else "finished")


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
    assert (project / "next-step-claude.md").read_text() == "Build the new feature\n"
    assert client.post(prefix + "/task", json=payload, headers=headers).status_code == 409
    configure(project, review=True)
    folder = scheduler.create_plan(project)
    scheduler.spawn_runner(folder)
    assert wait(lambda: folder.units() and folder.units()[0].get("review_status") == "pending")
    latest = client.get(prefix + "/task").get_json()
    response = client.post(prefix + "/task", json={"text": "another task", "base_hash": latest["hash"], "use_for_next_run": True}, headers=headers)
    assert response.status_code == 409
    assert (project / "task.md").read_text() == latest["text"]
    handoffs = client.get(prefix + f"/plans/{folder.id}/handoffs").get_json()["handoffs"]
    unit = handoffs[0]
    content = client.get(prefix + f"/plans/{folder.id}/handoffs/{unit['id']}/response").get_json()["content"]
    review = {"unit": unit["id"], "text": content + "\nReviewed by a human", "base_hash": sha256(content)}
    endpoint = prefix + f"/plans/{folder.id}/review"
    assert client.post(endpoint, json=review).status_code == 403
    assert client.post(endpoint, json=review, headers=headers).status_code == 200
    assert client.post(endpoint, json=review, headers=headers).status_code == 409
    assert wait(lambda: folder.load()["status"] == "finished")
