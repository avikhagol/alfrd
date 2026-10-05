"""Agents keep a progress checklist; a retried turn is told about the attempt before it."""

import json

import pytest
import yaml

from test_agent_loop import project, service  # noqa: F401
from alfrd.agent_loop import progress_file, retry_note
from alfrd.execution import ExecutionError, load_execution
from alfrd.runtime import scheduler


def prompts(root):
    return [json.loads(line)["prompt"] for line in (root / "calls.jsonl").read_text().splitlines()]


def test_progress_file_names():
    assert progress_file({}, "t1", own_workspace=True) == "PROGRESS.md"
    assert progress_file({}, "t1", own_workspace=False) == "PROGRESS-t1.md"      # tasks share one folder
    assert progress_file({"progress": "notes/{target}.md"}, "t1", True) == "notes/t1.md"
    assert progress_file({"progress": False}, "t1", True) is None


def test_retry_note_says_what_happened():
    previous = {"id": "0001-task-t001-claude", "status": "interrupted", "started": "2026-10-06T01:00:00",
                "finished": "2026-10-06T01:42:00", "error": "runner died"}
    note = retry_note(previous, 2, "PROGRESS.md")
    assert "attempt 2" in note and "was interrupted after 42 min" in note and "(runner died)" in note
    assert "read PROGRESS.md" in note and "git diff" in note
    assert retry_note(None, 1, "PROGRESS.md") == ""


def test_every_turn_is_asked_to_keep_the_checklist(project):
    folder = scheduler.create_plan(project)
    scheduler.Runner(project, folder.id).run()
    first = prompts(project)[0]
    assert "PROGRESS" in first and "continue from the first unchecked item" in first
    assert "RETRY" not in first
    assert folder.units()[0]["handoff"]["progress_file"].startswith("PROGRESS")


def test_a_retried_turn_is_told_about_the_failed_attempt(project, monkeypatch):
    monkeypatch.setenv("FAIL_AGENT", "1")
    folder = scheduler.create_plan(project)
    scheduler.Runner(project, folder.id).run()
    assert folder.load()["status"] == "failed"
    monkeypatch.delenv("FAIL_AGENT")
    scheduler.control(project, folder.id, "resume", retry_failed=True, spawn=False)
    scheduler.Runner(project, folder.id).run()
    retried = prompts(project)[1]
    first_unit = folder.units()[0]["id"]
    assert f"RETRY: this is attempt 2 of this turn. The previous attempt ({first_unit}) failed" in retried
    assert folder.units()[1]["attempt_number"] == 2


def test_progress_can_be_switched_off_and_is_checked(project):
    data = yaml.safe_load((project / "alfrd.yaml").read_text())
    data["loop"]["progress"] = False
    (project / "alfrd.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
    folder = scheduler.create_plan(project)
    scheduler.Runner(project, folder.id).run()
    assert "PROGRESS" not in prompts(project)[0]
    data["loop"]["progress"] = "../outside.md"
    (project / "alfrd.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
    with pytest.raises(ExecutionError, match="loop.progress"):
        load_execution(project)
