"""Agent sequences, per-task turns, live overrides, delays, fallback models, worktrees,
and the attempt records (usage, lineage, outcomes) the baseline report reads."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from test_agent_loop import RESPONSE, project, rewrite_plan_csv, service, task_client, wait  # noqa: F401
from alfrd.agent_io import agent_totals, codex_report, token_usage
from alfrd.agent_loop import approve_response, compact_report, expand_steps, reject_response, sha256, task_iterations
from alfrd.execution import ExecutionError, load_execution, parse_delay
from alfrd.runtime import scheduler


def edit(root, change):
    path = root / "alfrd.yaml"
    data = yaml.safe_load(path.read_text())
    change(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return data


def set_turns(root, iterations, sequence=None, turns=None):
    def change(data):
        repeat = data["workflows"][0]["repeat"]
        repeat["iterations"] = iterations
        if sequence:
            repeat["sequence"] = sequence
        if turns is not None:
            data["workflows"][0]["turns"] = turns
    edit(root, change)
    rewrite_plan_csv(root)


# -- sequences and iterations ---------------------------------------------

def test_sequence_counts_total_turns_and_follows_agents():
    workflow = {"repeat": {"iterations": 9, "sequence": ["claude", "claude", "claude", "codex", "codex", "claude"]}}
    steps = expand_steps(workflow, {})
    assert list(steps)[:7] == ["t001-claude", "t002-claude", "t003-claude", "t004-codex", "t005-codex", "t006-claude", "t007-claude"]
    assert len(steps) == 9
    assert steps["t003-claude"]["handoff"] == {"input": "{target}/next-step-claude.md", "output": "{target}/next-step-codex.md"}
    assert steps["t002-claude"]["handoff"]["output"] == "{target}/next-step-claude.md"
    assert steps["t009-claude"]["iteration"] == steps["t009-claude"]["iterations"] == 9


def test_passes_and_custom_passes():
    assert len(expand_steps({"repeat": {"passes": 3, "sequence": ["claude", "codex"]}}, {})) == 6
    custom = expand_steps({"repeat": {"iterations": 7, "sequence": [["claude", "codex"], ["claude", "claude", "codex"]]}}, {})
    assert [s["entrypoint"] for s in custom.values()] == ["claude", "codex", "claude", "claude", "codex", "claude", "claude"]
    with pytest.raises(ValueError, match="exactly one"):
        expand_steps({"repeat": {"iterations": 2, "passes": 1, "sequence": ["claude"]}}, {})
    with pytest.raises(ValueError, match="agent names"):
        expand_steps({"repeat": {"iterations": 2, "sequence": ["bad name"]}}, {})


def test_turn_overrides_apply_by_number_or_id():
    steps = expand_steps({"repeat": {"iterations": 3, "sequence": ["claude", "codex"]},
                          "turns": {2: {"human_review": True}, "t003-claude": {"manual": True, "after": "+1h"}}}, {})
    assert steps["t002-codex"]["human_review"] is True
    assert steps["t003-claude"]["manual"] is True and steps["t003-claude"]["after"] == "+1h"
    with pytest.raises(ValueError, match="accepts only"):
        expand_steps({"repeat": {"iterations": 1, "sequence": ["claude"]}, "turns": {1: {"cmd": ["x"]}}}, {})


def test_custom_sequence_runs_in_order(project):
    set_turns(project, 5, ["claude", "claude", "codex"])
    folder = scheduler.create_plan(project)
    scheduler.Runner(project, folder.id).run()
    calls = [json.loads(line)["agent"] for line in (project / "calls.jsonl").read_text().splitlines()]
    assert calls == ["claude", "claude", "codex", "claude", "claude"]
    assert folder.load()["status"] == "finished"
    assert folder.load()["loop"]["unit"] == "turns"
    assert [u["handoff"]["output"] for u in folder.units()][:3] == ["task/next-step-claude.md", "task/next-step-codex.md", "task/next-step-claude.md"]


def test_generic_agent_in_a_sequence(project, tmp_path):
    script = tmp_path / "gemini.py"
    script.write_text(f"import sys\nsys.stdin.read()\nprint({RESPONSE!r})\n")
    def change(data):
        data["entrypoint"].append({"name": "gemini", "cmd": [sys.executable, str(script)], "stdin_file": "{prompt_file}",
                                   "output_file": "{response_file}", "output_capture": "stdout"})
        data["workflows"][0]["repeat"].update(iterations=2, sequence=["claude", "gemini"])
    edit(project, change)
    rewrite_plan_csv(project)
    cfg = load_execution(project)
    assert [s.adapter for s in cfg.steps] == ["generic", "generic"]  # the fixture runs Python for every agent
    folder = scheduler.create_plan(project)
    scheduler.Runner(project, folder.id).run()
    assert folder.load()["status"] == "finished"
    assert folder.units()[1]["handoff"]["output"] == "task/next-step-claude.md"
    assert (project / "task/next-step-gemini.md").exists()
    assert folder.units()[1]["usage_source"] == "unavailable"


def test_task_turns_are_capped_by_the_project_maximum(service, tmp_path, project):
    client, headers, prefix = task_client(service, tmp_path)
    too_many = client.post(prefix + "/tasks", json={"name": "long", "task": "x", "iterations": 11}, headers=headers)
    assert too_many.status_code == 400 and "maximum of 10" in too_many.get_json()["error"]["message"]
    assert client.post(prefix + "/tasks", json={"name": "small", "task": "x", "iterations": 2}, headers=headers).status_code == 201
    assert json.loads((project / "small/.alfrd-task.json").read_text()) == {"iterations": 2, "version": 2}
    listing = client.get(prefix + "/tasks").get_json()
    assert listing["max_iterations"] == 10 and listing["iteration_unit"] == "turns"
    small = next(t for t in listing["tasks"] if t["name"] == "small")
    assert small["iterations"] == 2 and small["over_limit"] is False and small["runs"] == 0


def test_task_file_versions_convert_between_passes_and_turns():
    sequence = {"repeat": {"iterations": 10, "sequence": ["claude", "codex"]}}
    legacy = {"repeat": {"iterations": 5}, "steps": ["claude-turn", "codex-turn"]}
    assert task_iterations({"iterations": 3}, sequence) == 6          # version 1 counted passes
    assert task_iterations({"iterations": 3, "version": 2}, sequence) == 3
    assert task_iterations({"iterations": 3, "version": 2}, legacy) == 2
    assert task_iterations({"iterations": 3}, legacy) == 3


def test_parse_delay():
    assert parse_delay("+1h") == 3600 and parse_delay("90m") == 5400 and parse_delay("2h30m") == 9000
    assert parse_delay(45) == 45 and parse_delay(None) is None and parse_delay("0") is None
    for bad in ("soon", "8d", True, "-1h"):
        with pytest.raises(ValueError):
            parse_delay(bad)


# -- usage, prompt sizes, outcomes ------------------------------------------

def test_codex_input_is_normalized_and_provenance_recorded():
    usage, source = codex_report(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 5}}))
    assert source == "codex_event" and usage["input_uncached_tokens"] == 60
    usage, _ = codex_report(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 5}}))
    assert usage["input_uncached_tokens"] is None
    usage, source = codex_report("tokens used\n1,000\n")
    assert source == "codex_total_only" and usage["total_tokens"] == 1000
    assert usage["input_tokens"] is usage["cache_read_input_tokens"] is usage["input_uncached_tokens"] is None
    assert codex_report("nothing") == (None, "unavailable")
    assert token_usage({"input_tokens": 7, "output_tokens": 1})["input_uncached_tokens"] == 7


def test_totals_use_uncached_input_across_providers_and_old_records():
    units = [{"agent": "claude", "agent_usage": {"input_tokens": 10, "cache_read_input_tokens": 500, "output_tokens": 1}},
             {"argv": ["codex", "exec"], "agent_usage": {"input_tokens": 100, "cache_read_input_tokens": 40, "output_tokens": 2}}]
    totals = agent_totals(units)
    assert totals["input_uncached_tokens"] == 10 + 60
    assert totals["input_tokens"] == 110  # raw counters stay as reported


def test_prompt_size_capture(tmp_path):
    text = "<!-- ALFRD plan=p unit=u -->\nintro\n" * 3 + "## Goal\nKeep.\n## What was completed\n" + "x" * 9000 + "\n## Blockers\nNone\n"
    out, sizes = compact_report(text, 3000, tmp_path / "input.md")
    assert sizes["raw_input_chars"] == len(text)
    assert sizes["trimmed_handoff_chars"] == len(out) <= 3000
    assert sizes["sections_truncated"] == ["What was completed"]
    assert compact_report("## Goal\nshort\n", 3000, tmp_path / "x.md")[1]["sections_truncated"] == []


@pytest.mark.parametrize("failure, outcome", [(None, "accepted"), ("BAD_RESPONSE", "failed_validation"), ("FAIL_AGENT", "failed_runtime")])
def test_outcomes_and_labels(project, monkeypatch, failure, outcome):
    set_turns(project, 1)
    if failure:
        monkeypatch.setenv(failure, "1")
    folder = scheduler.create_plan(project)
    scheduler.Runner(project, folder.id).run()
    unit = folder.units()[0]
    assert unit["outcome"] == outcome
    assert unit["treatment"] == "baseline" and unit["run_kind"] == "production"
    assert unit["attempt_number"] == 1 and unit["retry_of"] is None and unit["logical_turn_id"]
    assert unit["handoff"]["raw_input_chars"] >= unit["handoff"]["trimmed_handoff_chars"]
    assert unit["handoff"]["sections_truncated"] == []
    if outcome == "failed_validation":
        assert "Missing heading" in unit["outcome_reason"]


def test_cancelled_turn_is_abandoned(project):
    (project / "hold").touch()
    folder = scheduler.create_plan(project)
    scheduler.spawn_runner(folder)
    try:
        assert wait(lambda: folder.units() and folder.units()[0].get("pid"))
        scheduler.control(project, folder.id, "cancel")
        assert wait(lambda: folder.load()["status"] == "cancelled")
        assert folder.units()[0]["outcome"] == "abandoned"
    finally:
        (project / "hold").unlink(missing_ok=True)


def test_retries_keep_one_logical_turn_across_plans(project, monkeypatch):
    set_turns(project, 1)
    monkeypatch.setenv("FAIL_AGENT", "1")
    first = scheduler.create_plan(project)
    scheduler.Runner(project, first.id).run()
    attempt = first.units()[0]
    second = scheduler.create_plan(project, retry_failed=True)   # reset_failed + relaunch in a new plan
    assert second.load()["retry_of"] == first.id
    scheduler.Runner(project, second.id).run()
    monkeypatch.delenv("FAIL_AGENT")
    third = scheduler.create_plan(project, retry_failed=True)
    scheduler.Runner(project, third.id).run()
    attempts = [first.units()[0], second.units()[0], third.units()[0]]
    assert {a["logical_turn_id"] for a in attempts} == {attempt["logical_turn_id"]}
    assert [a["attempt_number"] for a in attempts] == [1, 2, 3]
    assert attempts[1]["retry_of"] == f"{first.id}/{attempt['id']}"
    assert attempts[2]["outcome"] == "accepted"


def test_fresh_plan_gets_a_new_logical_turn(project):
    set_turns(project, 1)
    first = scheduler.create_plan(project)
    scheduler.Runner(project, first.id).run()
    rewrite_plan_csv(project)
    second = scheduler.create_plan(project)
    scheduler.Runner(project, second.id).run()
    assert first.units()[0]["logical_turn_id"] != second.units()[0]["logical_turn_id"]


# -- running-plan overrides: review, human turn, delay ----------------------

def test_review_added_while_turn_runs_holds_handoff_until_approved(project):
    set_turns(project, 2)
    (project / "hold").touch()
    folder = scheduler.create_plan(project)
    scheduler.spawn_runner(folder)
    try:
        assert wait(lambda: folder.units() and folder.units()[0].get("pid"))
        first = folder.units()[0]["steps"][0]
        with pytest.raises(ExecutionError, match="only its review"):
            scheduler.set_override(project, folder.id, first, {"manual": True})
        scheduler.set_override(project, folder.id, first, {"human_review": True})
        before = (project / "task/next-step-codex.md").read_text()
        (project / "hold").unlink()
        assert wait(lambda: folder.units()[0].get("review_status") == "pending")
        assert (project / "task/next-step-codex.md").read_text() == before
        assert len(folder.units()) == 1
        unit = folder.units()[0]
        text = Path(unit["handoff"]["response_file"]).read_text()
        approve_response(project, folder.id, unit["id"], text + "\nReviewed.", sha256(text))
        assert wait(lambda: folder.load()["status"] == "finished")
        assert "Reviewed." in (project / "task/next-step-codex.md").read_text()
        assert folder.units()[0]["outcome"] == "accepted"
        with pytest.raises(ExecutionError, match="already finished"):
            scheduler.set_override(project, folder.id, first, {"human_review": False})
    finally:
        (project / "hold").unlink(missing_ok=True)
        if folder.load()["status"] == "running":
            scheduler.control(project, folder.id, "cancel")


def test_rejected_review_stops_without_publishing(project):
    set_turns(project, 2, turns={1: {"human_review": True}})
    before = (project / "task/next-step-codex.md").read_text()
    folder = scheduler.create_plan(project)
    scheduler.spawn_runner(folder)
    try:
        assert wait(lambda: folder.units() and folder.units()[0].get("review_status") == "pending")
        reject_response(project, folder.id, folder.units()[0]["id"], "wrong direction")
        assert wait(lambda: folder.load()["status"] == "failed")
        unit = folder.units()[0]
        assert unit["outcome"] == "rejected" and unit["outcome_reason"] == "wrong direction"
        assert (project / "task/next-step-codex.md").read_text() == before
        assert len(folder.units()) == 1
    finally:
        if folder.load()["status"] == "running":
            scheduler.control(project, folder.id, "cancel")


def test_human_turn_chosen_per_step(project):
    set_turns(project, 2, turns={"t002-codex": {"manual": True}})
    cfg = load_execution(project)
    assert [s.manual for s in cfg.steps] == [False, True]
    folder = scheduler.create_plan(project)
    scheduler.spawn_runner(folder)
    try:
        assert wait(lambda: len(folder.units()) == 2 and folder.units()[1].get("manual"))
        from alfrd.agent_loop import submit_response
        submit_response(project, folder.id, folder.units()[1]["id"], RESPONSE)
        assert wait(lambda: folder.load()["status"] == "finished")
        calls = [json.loads(line)["agent"] for line in (project / "calls.jsonl").read_text().splitlines()]
        assert calls == ["claude"]
    finally:
        if folder.load()["status"] == "running":
            scheduler.control(project, folder.id, "cancel")


def test_delay_holds_next_step_and_run_now_releases_it(project):
    set_turns(project, 2, turns={2: {"after": "+1h"}})
    folder = scheduler.create_plan(project)
    scheduler.spawn_runner(folder)
    try:
        assert wait(lambda: any(w.get("kind") == "delay" for w in folder.load().get("waiting") or []))
        delayed = next(w for w in folder.load()["waiting"] if w.get("kind") == "delay")
        assert delayed["step"] == "t002-codex" and delayed["until"]
        assert len(folder.units()) == 1 and folder.load()["status"] == "running"
        scheduler.set_override(project, folder.id, "t002-codex", {"after": 0})   # Run now
        assert wait(lambda: folder.load()["status"] == "finished")
        assert len(folder.units()) == 2
    finally:
        if folder.load()["status"] == "running":
            scheduler.control(project, folder.id, "cancel")


def test_override_api_lists_and_changes_turns(service, tmp_path, project):
    client, headers, prefix = task_client(service, tmp_path)
    folder = scheduler.create_plan(project)
    url = prefix + f"/plans/{folder.id}/turns"
    turns = client.get(url).get_json()["turns"]
    assert len(turns) == 10 and turns[0]["state"] == "pending" and "manual" in turns[0]["editable"]
    response = client.post(url + "/t003-claude", json={"human_review": True, "after": "2h"}, headers=headers)
    assert response.status_code == 200 and response.get_json()["overrides"] == {"human_review": True, "after": "2h"}
    third = client.get(url).get_json()["turns"][2]
    assert third["human_review"] is True and third["after"] == 7200
    assert client.post(url + "/t003-claude", json={"cmd": "x"}, headers=headers).status_code == 400
    assert client.post(url + "/missing", json={"manual": True}, headers=headers).status_code == 400
    assert client.post(url + "/t003-claude", json={"human_review": None, "after": None}, headers=headers).get_json()["overrides"] == {}


# -- fallback models ---------------------------------------------------------

def test_fallback_model_retries_the_turn_after_a_provider_failure(project, tmp_path):
    script = tmp_path / "picky.py"
    script.write_text("import sys\nsys.stdin.read()\nmodel=sys.argv[sys.argv.index('--model')+1]\n"
                      f"sys.exit(3) if model != 'backup' else print({RESPONSE!r})\n")
    def change(data):
        data["entrypoint"][0].update(cmd=[sys.executable, str(script)], adapter="generic", model_option="--model",
                                     model="primary", fallback_models=["backup"])
    edit(project, change)
    set_turns(project, 1)
    folder = scheduler.create_plan(project)
    scheduler.Runner(project, folder.id).run()
    units = folder.units()
    assert folder.load()["status"] == "finished"
    assert [u["outcome"] for u in units] == ["failed_runtime", "accepted"]
    assert units[1]["requested_model"] == "backup" and units[1]["retry_of"] == f"{folder.id}/{units[0]['id']}"
    assert units[0]["logical_turn_id"] == units[1]["logical_turn_id"]


def test_fallback_never_follows_a_validation_failure(project, tmp_path, monkeypatch):
    def change(data):
        data["entrypoint"][0].update(adapter="generic", model_option="--model", fallback_models=["backup"])
    edit(project, change)
    set_turns(project, 1)
    monkeypatch.setenv("BAD_RESPONSE", "1")
    folder = scheduler.create_plan(project)
    scheduler.Runner(project, folder.id).run()
    assert folder.load()["status"] == "failed" and len(folder.units()) == 1


def test_claude_fallback_uses_the_native_option():
    from alfrd.agent_io import agent_command
    argv, _, _ = agent_command(["claude", "-p"], "opus", fallback="sonnet")
    assert argv[argv.index("--fallback-model") + 1] == "sonnet"


# -- per-task worktrees ------------------------------------------------------

@pytest.mark.skipif(not shutil.which("git"), reason="git is not installed")
def test_each_task_runs_in_its_own_worktree(project):
    git = lambda *a: subprocess.run(["git", "-C", str(project), *a], check=True, capture_output=True, text=True)
    git("init", "-q")
    git("-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "base")
    set_turns(project, 2)
    folder = scheduler.create_plan(project)
    workspace = project / "task" / "workspace"
    assert folder.load()["workspace"] == str(workspace)
    assert (workspace / ".git").is_file()
    assert "/task/workspace/" in (project / ".git/info/exclude").read_text()
    scheduler.Runner(project, folder.id).run()
    assert folder.load()["status"] == "finished"
    assert all(u["cwd"] == str(workspace) for u in folder.units())
    assert (workspace / "calls.jsonl").exists() and not (project / "calls.jsonl").exists()
    assert "task/workspace" not in git("status", "--porcelain").stdout
    assert git("branch", "--list", "alfrd/task").stdout.strip()


def test_project_outside_git_shares_one_tree_with_a_warning(project):
    folder = scheduler.create_plan(project)
    assert "workspace" not in folder.load()
    assert "not in a git repository" in folder.load()["workspace_warning"]


@pytest.mark.skipif(not shutil.which("git"), reason="git is not installed")
def test_uncommitted_project_folder_falls_back_to_one_tree(project):
    outer = project.parent
    git = lambda *a: subprocess.run(["git", "-C", str(outer), *a], check=True, capture_output=True, text=True)
    git("init", "-q")
    git("-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "base")
    folder = scheduler.create_plan(project)
    assert "workspace" not in folder.load()
    assert "not committed" in folder.load()["workspace_warning"]
    assert not (project / "task" / "workspace").exists()


@pytest.mark.skipif(not shutil.which("git"), reason="git is not installed")
def test_project_created_in_a_repository_gets_the_first_task_worktree(service, tmp_path):
    from alfrd.project_creation import create_project
    root = tmp_path / "repo"
    root.mkdir()
    git = lambda *a: subprocess.run(["git", "-C", str(root), *a], check=True, capture_output=True, text=True)
    git("init", "-q")
    (root / "code.py").write_text("print(1)\n")
    git("add", ".")
    git("-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-q", "-m", "code")
    create_project(service, root, template="agent-loop", task="Improve code.py")
    assert (root / "task" / "workspace" / "code.py").read_text() == "print(1)\n"
    assert "task/workspace" not in git("status", "--porcelain").stdout
