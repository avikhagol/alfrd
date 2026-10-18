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


FAKE_CLAUDE = """import json, sys
sys.stdin.read()
for i, cost in enumerate((0.2, 0.35, 0.5)):  # three results: a report and two background follow-ups
    print(json.dumps({"type": "result", "subtype": "success", "uuid": f"r{i}", "result": "answer" if i == 0 else "",
                      "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 100,
                                "cache_creation_input_tokens": 20}, "total_cost_usd": cost}), flush=True)
"""


def test_turn_cost_warning_and_summed_usage(pipeline, tmp_path):
    bins = tmp_path / "bin"
    bins.mkdir()
    (bins / "claude").write_text(f"#!{sys.executable}\n{FAKE_CLAUDE}")
    (bins / "claude").chmod(0o755)
    data = yaml.safe_load((pipeline / "alfrd.yaml").read_text())
    data["entrypoint"].append({"name": "claude", "cmd": [str(bins / "claude"), "-p"], "stdin_file": "reviews/{target}-prompt.md",
                               "output_file": "reviews/{target}-c.md", "output_capture": "stdout"})
    data["steps"]["ask"] = {"entrypoint": "claude"}
    data["workflows"][0]["steps"] = ["ask"]
    data["execution"]["warn_turn_cost_usd"] = 0.4
    (pipeline / "alfrd.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
    pc.create(pipeline / "alfrd.plan.csv", [{"target": "alpha"}], ["ask"], ["ask"], key_column="TARGET_NAME",
              files_column="FILENAMES", code_column="PROJECT_CODE", workdir_column="WORKDIR")
    folder = scheduler.create_plan(pipeline)
    scheduler.Runner(pipeline, folder.id).run()
    unit = folder.units()[-1]
    assert unit["status"] == "done"
    usage = unit["agent_usage"]
    assert (usage["input_tokens"], usage["output_tokens"], usage["cache_read_input_tokens"], usage["cache_creation_input_tokens"]) == (30, 15, 300, 60)
    assert unit["total_cost_usd"] == 0.5 and unit["cost_warning"] == {"limit": "warn_turn_cost_usd", "value": 0.5, "bound": 0.4}
    status = scheduler.plan_status(pipeline, folder.id)
    assert status["cost_warnings"] == {"warn_turn_cost_usd": 0.4, "units": [unit["id"]]}
    assert status["agent_totals"]["total_cost_usd"] == 0.5
    from alfrd.events import EVENTS_FILE

    kinds = [__import__("json").loads(line)["kind"] for line in (folder.path / EVENTS_FILE).read_text().splitlines()]
    assert kinds.count("budget.warning") == 1
    data["execution"]["warn_turn_cost_usd"] = -1
    (pipeline / "alfrd.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
    with pytest.raises(Exception, match="warn_turn_cost_usd"):
        load_execution(pipeline)
