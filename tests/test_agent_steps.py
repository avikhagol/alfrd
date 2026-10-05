"""Agent steps in any workflow (category: Agent, or a claude/codex command) behave like loop turns:
recorded as agents, a retry is told about the failed attempt, an earlier answer is kept, not refused."""

import sys

import pytest
import yaml

from alfrd.execution import load_execution
from alfrd.runtime import plan_csv as pc
from alfrd.runtime import scheduler

AGENT = """import os, pathlib, sys
prompt = sys.stdin.read()
pathlib.Path('prompts.log').open('a').write(prompt + '\\n=====\\n')
if pathlib.Path('fail-once').exists():
    pathlib.Path('fail-once').unlink()
    sys.exit(3)
print('review done')
"""


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "agent.py").write_text(AGENT)
    (root / "reviews").mkdir()
    (root / "reviews" / "alpha-prompt.md").write_text("Review the averaging of alpha.")
    (root / "alfrd.yaml").write_text(yaml.safe_dump({
        "version": 1, "name": "pipe",
        "entrypoint": [{"name": "step", "cmd": [sys.executable, "-c", "pass"]},
                       {"name": "reviewer", "cmd": [sys.executable, str(root / "agent.py")],
                        "stdin_file": "reviews/{target}-prompt.md", "output_file": "reviews/{target}-review.md",
                        "output_capture": "stdout"}],
        "execution": {"step_entrypoint": "step", "usage_interval": 0},
        "steps": {"review": {"category": "Agent", "entrypoint": "reviewer"}},
        "workflows": [{"name": "w", "steps": ["average", "review"]}],
    }, sort_keys=False))
    cols = dict(key_column="TARGET_NAME", files_column="FILENAMES", code_column="PROJECT_CODE", workdir_column="WORKDIR")
    pc.create(root / "alfrd.plan.csv", [{"target": "alpha"}], ["average", "review"], ["average", "review"], **cols)
    monkeypatch.setattr(scheduler, "POLL", .03)
    return root


def test_an_agent_category_step_is_an_agent_step(pipeline):
    steps = {s.id: s for s in load_execution(pipeline).steps}
    assert steps["review"].agent and not steps["average"].agent


def test_retrying_an_agent_step_tells_it_about_the_failed_attempt(pipeline):
    (pipeline / "fail-once").write_text("")
    folder = scheduler.create_plan(pipeline)
    scheduler.Runner(pipeline, folder.id).run()
    first = [u for u in folder.units() if u["steps"] == ["review"]][0]
    assert first["status"] == "failed" and first["agent_step"] and first["agent"] == "reviewer"
    (pipeline / "reviews" / "alpha-review.md").write_text("partial answer")   # left by the failed attempt
    scheduler.control(pipeline, folder.id, "resume", retry_failed=True, spawn=False)
    scheduler.Runner(pipeline, folder.id).run()
    second = [u for u in folder.units() if u["steps"] == ["review"]][-1]
    assert second["status"] == "done" and second["attempt_number"] == 2
    prompt = (pipeline / second["prompt_file"]).read_text()
    assert prompt.startswith("Review the averaging of alpha.") and "RETRY: this is attempt 2" in prompt
    assert (pipeline / "reviews" / "alpha-review.md").read_text().strip() == "review done"
    assert (pipeline / second["previous_output"]).read_text() == "partial answer"
    assert "agent_usage" in second  # recorded (None for a command without token reports)


def test_a_claude_step_streams_for_token_usage_when_it_has_an_output_file(pipeline):
    data = yaml.safe_load((pipeline / "alfrd.yaml").read_text())
    data["entrypoint"].append({"name": "claude", "cmd": ["claude", "-p", "--output-format", "text"],
                               "stdin_file": "reviews/{target}-prompt.md", "output_file": "reviews/{target}-c.md",
                               "output_capture": "stdout"})
    data["entrypoint"].append({"name": "claude-bare", "cmd": ["claude", "-p"]})
    data["steps"].update(ask={"entrypoint": "claude"}, ask_bare={"entrypoint": "claude-bare"})
    data["workflows"][0]["steps"] += ["ask", "ask_bare"]
    (pipeline / "alfrd.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
    steps = {s.id: s for s in load_execution(pipeline).steps}
    assert steps["ask"].agent and steps["ask"].claude_stream and "stream-json" in steps["ask"].argv
    assert steps["ask_bare"].agent and not steps["ask_bare"].claude_stream   # no file to put the answer in: plain text
