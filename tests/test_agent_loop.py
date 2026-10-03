import json
import os
from pathlib import Path
import signal
import sys
import time

import pytest
import yaml

from alfrd.api import status as api
from alfrd.execution import load_execution
from alfrd.project_creation import create_project
from alfrd.runtime import RuntimeService, RuntimeStore
from alfrd.runtime import scheduler
from alfrd.agent_loop import submit_response


RESPONSE = "\n".join(f"## {heading}\nChecked." for heading in (
    "Goal", "What was completed", "Files changed", "Checks and results",
    "Instructions for the next agent", "Acceptance criteria", "Blockers"))


@pytest.fixture
def service(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    return RuntimeService(store)


@pytest.fixture
def project(tmp_path, service, monkeypatch):
    root = tmp_path / "project"
    create_project(service, root, template="agent-loop", task="Fix the widget", iterations=5)
    script = tmp_path / "fake_agent.py"
    script.write_text(
        "import os,pathlib,sys,time\n"
        "incoming=sys.stdin.read()\n"
        "agent=sys.argv[1]\n"
        "with open('calls.jsonl','a') as f: f.write(__import__('json').dumps({'agent':agent,'prompt':incoming})+'\\n')\n"
        "print('stderr diagnostic',file=sys.stderr)\n"
        "hold=pathlib.Path('hold')\n"
        "while hold.exists(): time.sleep(.05)\n"
        f"text={RESPONSE!r}+'\\nAgent: '+agent\n"
        "if os.environ.get('BAD_RESPONSE'): text='empty contract'\n"
        "if os.environ.get('FAIL_AGENT'): sys.exit(7)\n"
        "if agent=='codex': pathlib.Path(sys.argv[2]).write_text(text)\n"
        "else: print(text)\n"
    )
    data = yaml.safe_load((root / "alfrd.yaml").read_text())
    for entry in data["entrypoint"]:
        entry["cmd"] = [sys.executable, str(script), entry["name"], "{response_file}"]
    data["execution"]["usage_interval"] = 0
    (root / "alfrd.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
    monkeypatch.setattr(scheduler, "POLL", .03)
    yield root
    for plan in scheduler.list_plans(root):
        if plan.get("status") in ("running", "paused", "interrupted"):
            scheduler.control(root, plan["id"], "cancel")


def wait(predicate, timeout=20):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if predicate():
            return True
        time.sleep(.05)
    return False


def test_scaffold_and_register_ordered_workflow(service, tmp_path):
    root = tmp_path / "p"
    project, workflows = create_project(service, root, template="agent-loop", task="Build it")
    assert project.name == "p"
    assert len(workflows) == 1
    persisted = service.get_workflow(workflows[0].id)
    assert [s.key for s in persisted.steps] == load_execution(root).step_ids
    assert len(persisted.steps) == 10
    assert (root / "next-step-claude.md").read_text() == "Build it\n"
    assert len(scheduler.table_for(load_execution(root), root / "alfrd.plan.csv").rows) == 1
    before = (root / "alfrd.yaml").read_bytes()
    with pytest.raises(FileExistsError):
        create_project(service, root, task="other")
    assert (root / "alfrd.yaml").read_bytes() == before


@pytest.mark.parametrize("iterations", [0, -1, 101, True, "5"])
def test_invalid_iterations_leave_no_files(service, tmp_path, iterations):
    root = tmp_path / "p"
    with pytest.raises(ValueError):
        create_project(service, root, template="agent-loop", task="task", iterations=iterations)
    assert not root.exists()


def test_basic_project_and_cli(service, tmp_path):
    from typer.testing import CliRunner
    from alfrd.cli import alfrd_cli

    result = CliRunner().invoke(alfrd_cli, ["projects", "create", str(tmp_path / "basic"),
                                          "--db", str(tmp_path / "cli.sqlite")])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "basic" / "alfrd.yaml").exists()
    assert not (tmp_path / "basic" / "next-step-claude.md").exists()


def test_five_cycles_feed_each_handoff_and_stop(project):
    folder = scheduler.create_plan(project)
    scheduler.Runner(project, folder.id).run()
    calls = [json.loads(line) for line in (project / "calls.jsonl").read_text().splitlines()]
    assert [c["agent"] for c in calls] == ["claude", "codex"] * 5
    assert "Fix the widget" in calls[0]["prompt"]
    assert "first turn" in calls[0]["prompt"]
    assert all("Agent: " + calls[i - 1]["agent"] in calls[i]["prompt"] for i in range(1, 10))
    assert "final turn" in calls[-1]["prompt"]
    assert folder.load()["status"] == "finished"
    assert len(folder.units()) == 10
    for unit in folder.units():
        assert unit["status"] == "done"
        assert "stderr diagnostic" not in Path(unit["handoff"]["response_file"]).read_text()
        assert unit["artifact"]["sha256"]
    doc = api.plan_status(project, folder.id)
    assert doc["loop"]["iteration"] == doc["loop"]["iterations"] == 5
    assert doc["counts"]["done"] == 10
    cursor = doc["cursor"]
    assert api.plan_status(project, folder.id, since=cursor)["changed"] is False


@pytest.mark.parametrize("failure", ["BAD_RESPONSE", "FAIL_AGENT"])
def test_failed_turn_never_publishes_or_starts_next(project, monkeypatch, failure):
    monkeypatch.setenv(failure, "1")
    old = (project / "next-step-codex.md").read_bytes()
    folder = scheduler.create_plan(project)
    scheduler.Runner(project, folder.id).run()
    assert folder.load()["status"] == "failed"
    assert len(folder.units()) == 1
    assert (project / "next-step-codex.md").read_bytes() == old


def test_pause_manual_response_and_resume(project):
    data = yaml.safe_load((project / "alfrd.yaml").read_text())
    data["entrypoint"][0]["manual"] = True
    (project / "alfrd.yaml").write_text(yaml.safe_dump(data))
    folder = scheduler.create_plan(project)
    scheduler.spawn_runner(folder)
    assert wait(lambda: any(u.get("manual") for u in folder.units()))
    unit = folder.units()[0]
    assert api.plan_status(project, folder.id)["loop"]["phase"] == "awaiting_response"
    scheduler.control(project, folder.id, "pause")
    submit_response(project, folder.id, unit["id"], RESPONSE)
    assert wait(lambda: folder.load()["status"] == "paused")
    assert len(folder.units()) == 1
    with pytest.raises(ValueError):
        submit_response(project, folder.id, unit["id"], RESPONSE)
    # Switch only invocation mode for this test by completing remaining manual turns.
    scheduler.control(project, folder.id, "resume")
    submitted = {unit["id"]}
    until = time.monotonic() + 30
    while time.monotonic() < until and folder.load()["status"] != "finished":
        for item in folder.units():
            if item.get("manual") and item["status"] == "running" and item["id"] not in submitted:
                submit_response(project, folder.id, item["id"], RESPONSE)
                submitted.add(item["id"])
        time.sleep(.1)
    assert folder.load()["status"] == "finished"


def test_runner_death_keeps_prompt_and_handoff(project):
    (project / "hold").touch()
    folder = scheduler.create_plan(project)
    scheduler.spawn_runner(folder)
    try:
        assert wait(lambda: folder.units() and folder.units()[0].get("pid"))
        unit = folder.units()[0]
        os.kill(folder.load()["runner"]["pid"], signal.SIGKILL)
        assert wait(lambda: not folder.runner_alive())
        assert scheduler.pid_alive(unit["pid"], unit["proc_start"])
        scheduler.reconcile(project)
        (project / "hold").unlink()
        assert wait(lambda: folder.load()["status"] == "finished", 35)
        assert len(folder.units()) == 10
        assert folder.units()[0]["handoff"]["prompt_sha256"] == unit["handoff"]["prompt_sha256"]
    finally:
        (project / "hold").unlink(missing_ok=True)
        if folder.load()["status"] != "finished":
            scheduler.control(project, folder.id, "cancel")


def test_studio_create_handoffs_and_status_api(service, tmp_path, project):
    from alfrd.gui import create_app
    from alfrd.gui.services import RuntimeCatalogReader

    app = create_app({"TESTING": True, "SECRET_KEY": "k", "RUNTIME_SERVICE": service,
                      "CATALOG_READER": RuntimeCatalogReader(service),
                      "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'catalog.sqlite'}", "STUDIO_PROJECTS": []})
    client = app.test_client()
    headers = {"X-CSRF-Token": client.get("/api/studio/session").get_json()["csrf_token"]}
    payload = {"path": str(tmp_path / "new"), "template": "agent-loop", "task": "task", "iterations": 2}
    assert client.post("/api/studio/projects/create", json=payload).status_code == 403
    response = client.post("/api/studio/projects/create", json=payload, headers=headers)
    assert response.status_code == 201, response.get_json()
    identifier = response.get_json()["identifier"]
    assert identifier in app.config["STUDIO_PROJECTS"]
    assert client.post("/api/studio/projects/create", json=payload, headers=headers).status_code == 409
    prefix = f"/api/studio/projects/{identifier}"
    initial = client.get(prefix + "/handoff?file=next-step-claude.md").get_json()
    saved = client.post(prefix + "/handoff", json={"file": initial["file"], "text": "changed", "base_hash": initial["hash"]}, headers=headers)
    assert saved.status_code == 200
    stale = client.post(prefix + "/handoff", json={"file": initial["file"], "text": "stale", "base_hash": initial["hash"]}, headers=headers)
    assert stale.status_code == 409
    assert client.get(prefix + "/handoff?file=../../secret.md").status_code == 400
    folder = scheduler.create_plan(tmp_path / "new")
    public = client.get(f"/api/v1/projects/{identifier}/plans/{folder.id}")
    assert public.status_code == 200
    assert public.get_json()["loop"]["iterations"] == 2
    archive = folder.path / "handoffs" / "manual-turn"
    archive.mkdir(parents=True)
    content = "aé🎯" * 70000
    prompt = archive / "prompt.md"
    prompt.write_text(content)
    response_path = archive / "response.md"
    unit = {"id": "manual-turn", "manual": True, "status": "running", "agent": "claude", "iteration": 1,
            "error": "a full diagnostic", "log": "unit.log", "handoff": {"prompt_file": str(prompt), "response_file": str(response_path)}}
    folder.save_unit(unit)
    url = prefix + f"/plans/{folder.id}"
    listing = client.get(url + "/handoffs").get_json()["handoffs"][0]
    assert "prompt_file" not in listing and "response_file" not in listing
    assert listing["prompt_bytes"] == len(content.encode())
    assert listing["error"] == "a full diagnostic" and listing["log"] == "unit.log"
    chunks, offset = [], 0
    while True:
        page = client.get(url + f"/handoffs/manual-turn/prompt?offset={offset}&limit=999999").get_json()
        assert page["returned_bytes"] <= 256 * 1024
        assert page["offset"] == offset
        assert page["returned_bytes"] == len(page["content"].encode())
        chunks.append(page["content"])
        offset += page["returned_bytes"]
        assert page["truncated"] == (offset < page["total_bytes"])
        if not page["truncated"]:
            break
    assert "".join(chunks) == content
    assert client.get(url + "/handoffs/manual-turn/unknown").status_code == 400
    assert client.get(url + "/handoffs/unknown/prompt").status_code == 404
    assert client.get(url + "/handoffs/../prompt").status_code == 400
    assert client.get(url + "/handoffs/manual-turn/prompt?offset=2").status_code == 400
    bad = client.post(url + "/response", json={"unit": unit["id"], "text": "wrong"}, headers=headers)
    assert bad.status_code == 400 and "errors" in bad.get_json()
    assert not response_path.exists()
    assert folder.units()[0]["status"] == "running"
    # Even a unit record pointing at another project file cannot expose it.
    unit["handoff"]["prompt_file"] = str(tmp_path / "new/task.md")
    folder.save_unit(unit)
    assert client.get(url + "/handoffs/manual-turn/prompt").status_code == 400



def test_missing_input_and_outside_handoff_stop_before_execution(project):
    (project / "next-step-claude.md").unlink()
    folder = scheduler.create_plan(project)
    scheduler.Runner(project, folder.id).run()
    assert len(folder.units()) == 1 and folder.units()[0]["status"] == "failed"
    assert not (project / "calls.jsonl").exists()
    data = yaml.safe_load((project / "alfrd.yaml").read_text())
    data["workflows"][0]["steps"][0]["handoff"]["input"] = "../outside.md"
    (project / "alfrd.yaml").write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError, match="inside"):
        load_execution(project)


def test_edited_outgoing_handoff_is_preserved(project):
    (project / "hold").touch()
    folder = scheduler.create_plan(project)
    scheduler.spawn_runner(folder)
    assert wait(lambda: folder.units() and folder.units()[0].get("pid"))
    outgoing = project / "next-step-codex.md"
    outgoing.write_text("User's revised next task")
    (project / "hold").unlink()
    assert wait(lambda: folder.load()["status"] == "failed")
    assert outgoing.read_text() == "User's revised next task"
    assert len(folder.units()) == 1


def test_workspace_lock_blocks_another_loop(project, tmp_path):
    import shutil

    other = tmp_path / "other"
    other.mkdir()
    for name in ("alfrd.yaml", "alfrd.plan.csv", "next-step-claude.md", "next-step-codex.md"):
        shutil.copyfile(project / name, other / name)
    data = yaml.safe_load((other / "alfrd.yaml").read_text())
    data["execution"]["cwd"] = str(project)
    (other / "alfrd.yaml").write_text(yaml.safe_dump(data))
    (project / "hold").touch()
    first = scheduler.create_plan(project)
    scheduler.spawn_runner(first)
    try:
        assert wait(lambda: first.units() and first.units()[0].get("pid"))
        second = scheduler.create_plan(other)
        assert scheduler.Runner(other, second.id).run() == 3
        assert second.load()["status"] == "interrupted"
        assert not second.units()
    finally:
        scheduler.control(project, first.id, "cancel")
        (project / "hold").unlink(missing_ok=True)
        assert wait(lambda: first.load()["status"] == "cancelled")


def test_changed_workflow_stops_at_turn_boundary(project):
    (project / "hold").touch()
    folder = scheduler.create_plan(project)
    scheduler.spawn_runner(folder)
    assert wait(lambda: folder.units() and folder.units()[0].get("pid"))
    path = project / "alfrd.yaml"
    path.write_text(path.read_text() + "\n# changed during execution\n")
    (project / "hold").unlink()
    assert wait(lambda: folder.load()["status"] == "failed")
    assert "workflow changed" in folder.load()["error"]
    assert len(folder.units()) == 1


def test_total_runtime_limit_stops_active_turn(project):
    data = yaml.safe_load((project / "alfrd.yaml").read_text())
    data["execution"]["max_runtime"] = 2
    data["execution"]["kill_grace"] = .2
    (project / "alfrd.yaml").write_text(yaml.safe_dump(data))
    (project / "hold").touch()
    folder = scheduler.create_plan(project)
    scheduler.Runner(project, folder.id).run()
    assert folder.load()["status"] == "failed"
    assert folder.load()["error"] == "total runtime limit reached"
    assert len(folder.units()) <= 1


def test_status_schema_and_read_only_cursor(project):
    from importlib.resources import files
    from jsonschema import validate

    folder = scheduler.create_plan(project)
    before = folder.plan_file.read_bytes()
    first = api.plan_status(project, folder.id)
    schema = json.loads(files("alfrd.schemas").joinpath("plan_status.v1.json").read_text())
    validate(first, schema)
    assert folder.plan_file.read_bytes() == before
    unit = {"id": "manual-turn", "iteration": 1, "agent": "claude", "manual": True,
            "status": "running", "steps": ["i001-claude-turn"], "row": "task", "rows": ["task"], "handoff": {}}
    folder.save_unit(unit)
    second = api.plan_status(project, folder.id, since=first["cursor"])
    assert second["changed"] is True
    assert second["loop"]["phase"] == "awaiting_response"


@pytest.mark.parametrize("heading", [
    "Instructions for the next agent (Codex)",
    "Instructions for the next agent (Claude) ##",
])
def test_handoff_accepts_recipient_annotation(tmp_path, heading):
    from alfrd.agent_loop import publish

    response = tmp_path / "response.md"
    response.write_text(RESPONSE.replace("Instructions for the next agent", heading))
    artifact = publish(tmp_path, {"response_file": str(response), "output": "next.md",
                                 "output_base_hash": None},
                       plan_id="test", unit_id="turn", iteration=1, agent="claude")
    assert artifact["path"] == "next.md"
    assert heading in (tmp_path / "next.md").read_text()


def test_handoff_rejects_missing_instructions(tmp_path):
    from alfrd.agent_loop import publish

    response = tmp_path / "response.md"
    response.write_text(RESPONSE.replace("## Instructions for the next agent", "## Unrelated instructions"))
    with pytest.raises(ValueError, match="Instructions for the next agent"):
        publish(tmp_path, {"response_file": str(response), "output": "next.md",
                           "output_base_hash": None},
                plan_id="test", unit_id="turn", iteration=1, agent="claude")
    assert not (tmp_path / "next.md").exists()


def test_response_contract_and_default_parity(project):
    import re
    from alfrd.agent_loop import (DEFAULT_ITERATIONS, MAX_ITERATIONS, MAX_RESPONSE_BYTES,
                                 REQUIRED_HEADINGS, validate_response)
    from alfrd.studio_defs import template_path

    assert validate_response(RESPONSE) == []
    for heading in REQUIRED_HEADINGS:
        assert f"Missing heading: ## {heading}" in validate_response(RESPONSE.replace(f"## {heading}", "## Typo"))
    assert validate_response("") == ["Response is empty"]
    assert validate_response("x" * (MAX_RESPONSE_BYTES + 1)) == ["Response exceeds 10 MiB"]
    web = Path(__file__).parents[1] / "src/alfrd/web/js/data/defs.js"
    constants = dict(re.findall(r"export const (DEFAULT_ITERATIONS|MAX_ITERATIONS) = (\d+);", web.read_text()))
    assert int(constants["DEFAULT_ITERATIONS"]) == DEFAULT_ITERATIONS
    assert int(constants["MAX_ITERATIONS"]) == MAX_ITERATIONS
    assert yaml.safe_load(template_path("agent-loop").read_text())["workflows"][0]["repeat"]["iterations"] == DEFAULT_ITERATIONS
    folder = scheduler.create_plan(project)
    scheduler.Runner(project, folder.id).run()
    prompt = Path(folder.units()[0]["handoff"]["prompt_file"]).read_text()
    assert all(f"## {h}" in prompt for h in REQUIRED_HEADINGS)


def test_invalid_manual_response_keeps_waiting_then_recovers(project):
    from alfrd.agent_loop import MAX_RESPONSE_BYTES, ResponseValidationError
    data = yaml.safe_load((project / "alfrd.yaml").read_text())
    data["entrypoint"][0]["manual"] = True
    data["workflows"][0]["repeat"]["iterations"] = 1
    (project / "alfrd.yaml").write_text(yaml.safe_dump(data))
    folder = scheduler.create_plan(project)
    scheduler.spawn_runner(folder)
    assert wait(lambda: folder.units() and folder.units()[0].get("handoff"))
    unit = folder.units()[0]
    for text, expected in [(RESPONSE.replace("## Blockers", "## Blokers"), "Missing heading: ## Blockers"),
                           ("", "Response is empty"), ("x" * (MAX_RESPONSE_BYTES + 1), "Response exceeds 10 MiB")]:
        with pytest.raises(ResponseValidationError) as caught:
            submit_response(project, folder.id, unit["id"], text)
        assert expected in caught.value.errors
        assert not Path(unit["handoff"]["response_file"]).exists()
        assert folder.units()[0]["status"] == "running"
    from typer.testing import CliRunner
    from alfrd.cli import alfrd_cli
    draft = project / "draft.md"
    draft.write_text("wrong")
    runner = CliRunner()
    args = ["plan", "response", folder.id, unit["id"], str(draft), "-C", str(project)]
    rejected = runner.invoke(alfrd_cli, args)
    assert rejected.exit_code != 0 and "Missing heading" in rejected.output
    assert not Path(unit["handoff"]["response_file"]).exists()
    draft.write_text(RESPONSE)
    accepted = runner.invoke(alfrd_cli, args)
    assert accepted.exit_code == 0, accepted.output
    assert wait(lambda: folder.load()["status"] == "finished")
    assert len(folder.units()) == 2
    assert folder.units()[0]["artifact"]


def test_scaffold_uses_reversed_workflow(service, tmp_path, monkeypatch):
    import alfrd.project_creation as creation
    from alfrd.studio_defs import template_path
    template = yaml.safe_load(template_path("agent-loop").read_text())
    template["workflows"][0]["steps"].reverse()
    source = tmp_path / "reversed.yaml"
    source.write_text(yaml.safe_dump(template))
    monkeypatch.setattr(creation, "template_path", lambda name: source)
    root = tmp_path / "reversed"
    create_project(service, root, template="agent-loop", task="Codex starts")
    assert (root / "next-step-codex.md").read_text() == "Codex starts\n"
    assert (root / "next-step-claude.md").read_text() == "# Awaiting the first codex plan\n"


def test_launch_failure_calls_after_failure_once(project, monkeypatch):
    folder = scheduler.create_plan(project)
    calls = []
    original = scheduler.Runner.after_failure

    def record(self, rows, step):
        calls.append(step)
        return original(self, rows, step)

    def fail(*args, **kwargs):
        raise OSError("launch failed")

    monkeypatch.setattr(scheduler.Runner, "after_failure", record)
    monkeypatch.setattr(scheduler.subprocess, "Popen", fail)
    scheduler.Runner(project, folder.id).run()
    assert calls == ["i001-claude-turn"]
    assert folder.load()["status"] == "failed"


def test_hidden_manifest_runs_loop(project):
    (project / "alfrd.yaml").rename(project / ".alfrd.yaml")
    folder = scheduler.create_plan(project)
    scheduler.Runner(project, folder.id).run()
    assert folder.load()["status"] == "finished"
    assert len(folder.units()) == 10


def test_custom_headings_and_contract_survive_manual_submission(project):
    from alfrd.agent_loop import prepare, publish, ResponseValidationError

    manifest = project / "alfrd.yaml"
    data = yaml.safe_load(manifest.read_text())
    data["loop"] = {"headings": ["Outcome", "Next"], "contract": {
        "first": "Inspect the custom goal.", "middle": "Continue the custom goal.",
        "final": "Close the custom goal."}}
    manifest.write_text(yaml.safe_dump(data))
    cfg = load_execution(project)
    folder = scheduler.create_plan(project)
    for step, wording in [(cfg.steps[0], "Inspect"), (cfg.steps[1], "Continue"), (cfg.steps[-1], "Close")]:
        archive = folder.path / "handoffs" / step.id
        handoff = prepare(project, archive, step, folder.id, step.id)
        assert wording + " the custom goal." in Path(handoff["prompt_file"]).read_text()
        assert "## Outcome; ## Next" in Path(handoff["prompt_file"]).read_text()
    unit = {"id": cfg.steps[-1].id, "status": "running", "manual": True, "iteration": 5, "handoff": handoff}
    folder.save_unit(unit)
    with pytest.raises(ResponseValidationError, match="Next"):
        submit_response(project, folder.id, unit["id"], "## Outcome\nDone")
    assert not Path(handoff["response_file"]).exists()
    submit_response(project, folder.id, unit["id"], "## Outcome\nDone\n## Next\nFinished")
    publish(project, handoff, plan_id=folder.id, unit_id=unit["id"], iteration=5, agent="codex")


def test_studio_refuses_skip_on_loop_cell(service, tmp_path, project):
    from alfrd.gui import create_app
    from alfrd.gui.services import RuntimeCatalogReader

    app = create_app({"TESTING": True, "SECRET_KEY": "k", "RUNTIME_SERVICE": service,
                      "CATALOG_READER": RuntimeCatalogReader(service),
                      "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'skip.sqlite'}"})
    client = app.test_client()
    headers = {"X-CSRF-Token": client.get("/api/studio/session").get_json()["csrf_token"]}
    folder = scheduler.create_plan(project)
    identifier = service.list_projects()[0].identifier
    url = f"/api/studio/projects/{identifier}/plans/{folder.id}/cells"
    before = folder.csv_path(folder.load()).read_bytes()
    for value in ("skip", ""):
        response = client.post(url, json={"cells": [{"row": "task", "step": "i001-claude-turn", "value": value}]}, headers=headers)
        assert response.status_code == 400
        assert "cannot be skipped" in response.get_json()["error"]["message"]
        assert folder.csv_path(folder.load()).read_bytes() == before


def test_cli_unused_iterations_and_template_validation(tmp_path):
    from typer.testing import CliRunner
    from alfrd.cli import alfrd_cli

    args = ["projects", "create", str(tmp_path / "basic"), "--db", str(tmp_path / "cli.sqlite")]
    response = CliRunner().invoke(alfrd_cli, args + ["--iterations", "5"])
    assert response.exit_code == 0
    assert "warning: --iterations is ignored" in response.output
    response = CliRunner().invoke(alfrd_cli, args + ["--template", "../../unknown"])
    assert response.exit_code != 0
    assert "unknown project template" in response.output


def test_project_agent_folder_and_bash_settings(project):
    from alfrd.agent_io import agent_command

    manifest = project / "alfrd.yaml"
    data = yaml.safe_load(manifest.read_text())
    data["project_settings"] = {"agent_access": {"folders": ["../reference notes", "../other notes"],
        "bash_commands": ["git status *", "pytest *"], "sandbox": "workspace-write"}}
    data["entrypoint"][0]["cmd"] = ["claude", "-p"]
    data["entrypoint"][1]["cmd"] = ["codex", "exec", "-"]
    manifest.write_text(yaml.safe_dump(data))
    cfg = load_execution(project)
    claude, codex = cfg.steps[0].argv, cfg.steps[1].argv
    expected = str((project / "../reference notes").resolve())
    assert claude[claude.index("--add-dir") + 1] == expected
    assert claude[claude.index("--add-dir") + 2] == str((project / "../other notes").resolve())
    assert codex.count("--add-dir") == 2
    assert claude[claude.index("--allowedTools") + 1] == "Bash(git status *),Bash(pytest *)"
    assert codex[codex.index("--add-dir") + 1] == expected
    assert codex[codex.index("--sandbox") + 1] == "workspace-write"
    assert codex[-1] == "-"
    assert agent_command(["custom", "run"], access={"folders": ["/tmp"]})[0] == ("custom", "run")


def test_studio_loop_options_and_cell_errors(service, tmp_path, project, monkeypatch):
    from alfrd.gui import create_app
    from alfrd.gui.services import RuntimeCatalogReader

    manifest = project / "alfrd.yaml"
    data = yaml.safe_load(manifest.read_text())
    data["loop"] = {"task_row": "custom-task"}
    manifest.write_text(yaml.safe_dump(data))
    folder = scheduler.create_plan(project)
    app = create_app({"TESTING": True, "RUNTIME_SERVICE": service,
                      "CATALOG_READER": RuntimeCatalogReader(service),
                      "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'p0.sqlite'}"})
    client = app.test_client()
    headers = {"X-CSRF-Token": client.get("/api/studio/session").get_json()["csrf_token"]}
    prefix = f"/api/studio/projects/{service.list_projects()[0].identifier}"
    assert client.get(prefix + "/execution").get_json()["loop"]["task_row"] == "custom-task"
    assert client.post(prefix + "/plans/bad!id/cells", json={}, headers=headers).status_code == 400
    def fail_load(self):
        raise OSError("unreadable plan")
    monkeypatch.setattr(scheduler.PlanDir, "load", fail_load)
    response = client.post(prefix + f"/plans/{folder.id}/cells", json={}, headers=headers)
    assert response.status_code == 400
    assert "unreadable plan" in response.get_json()["error"]["message"]
    monkeypatch.undo()
