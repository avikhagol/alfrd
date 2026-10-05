from typer.testing import CliRunner

from alfrd import baseline
from alfrd.cli import alfrd_cli
from alfrd.runtime import scheduler


def unit(plan, n, turn, *, attempt=1, outcome="accepted", usage=None, treatment="baseline", run_kind="production",
         truncated=(), retry_of=None):
    usage = {"input_tokens": 100, "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 10,
             "output_tokens": 50, "input_uncached_tokens": 100} if usage is None else usage
    return {"id": f"{n:04d}-task-t001-claude", "plan": plan, "steps": ["t001-claude"], "row": "task",
            "status": "done" if outcome == "accepted" else "failed", "started": f"2026-10-05T10:{n:02d}:00",
            "logical_turn_id": turn, "attempt_number": attempt, "retry_of": retry_of, "treatment": treatment,
            "run_kind": run_kind, "outcome": outcome, "agent": "claude", "usage_source": "claude_result",
            "agent_usage": usage, "model": "m",
            "handoff": {"raw_input_chars": 900, "trimmed_handoff_chars": 800, "prompt_chars": 1000,
                        "sections_truncated": list(truncated), "input_sha256": "i", "prompt_sha256": "p"}}


def make(root, spec):
    plans = {}
    for plan_id, items in spec.items():
        folder = scheduler.PlanDir(root, plan_id)
        folder.path.mkdir(parents=True)
        folder.save({"id": plan_id, "created": items[0]["started"], "status": "finished"})
        for item in items:
            folder.save_unit(item)
        plans[plan_id] = folder
    return plans


def test_turns_span_plans_and_count_failed_attempts(tmp_path):
    missing = {"output_tokens": 5, "input_uncached_tokens": None, "cache_read_input_tokens": None, "cache_creation_input_tokens": None}
    make(tmp_path, {
        "p1": [unit("p1", 1, "A", outcome="failed_runtime", usage=missing),
               unit("p1", 2, "X", treatment="trimmed")],
        "p2": [unit("p2", 3, "A", attempt=2, retry_of="p1/0001-task-t001-claude"),
               unit("p2", 4, "B", outcome="failed_validation", truncated=["What was completed"]),
               unit("p2", 5, "C")],
    })
    doc = baseline.report(tmp_path, count=3, exclude={"B": "provider status page incident 10:04"})
    turns = doc["turns"]
    assert [t["logical_turn_id"] for t in turns] == ["A", "C"]       # X is an experiment, B an outage
    assert doc["skipped"]["not_baseline"] == 1
    assert doc["excluded"][0]["turn"] == "B" and "incident" in doc["excluded"][0]["evidence"]
    a = turns[0]
    assert len(a["attempts"]) == 2 and a["accepted"] == 1
    assert a["usage"]["output_tokens"] == 55                           # the failed attempt's usage counts
    assert a["usage"]["input_uncached_tokens"] == 100 and a["complete"]["input_uncached_tokens"] is False
    assert doc["summary"]["extra_attempts"] == 1
    assert doc["readiness"]["ready"] is False                          # only 2 of 3 turns, and coverage
    assert any("only 2 of 3" in r for r in doc["readiness"]["reasons"])


def test_coverage_bar_is_exactly_eighty_percent(tmp_path):
    missing = {"input_uncached_tokens": None, "cache_read_input_tokens": 1, "cache_creation_input_tokens": 1, "output_tokens": 1}
    good = [unit("p", i, f"T{i}") for i in range(1, 9)]
    make(tmp_path, {"p": good + [unit("p", 9, "T9", usage=missing), unit("p", 10, "T10", usage=missing)]})
    doc = baseline.report(tmp_path, count=10)
    assert doc["coverage"]["input_uncached_tokens"]["coverage"] == 0.8 and doc["coverage"]["input_uncached_tokens"]["usable"]
    assert doc["readiness"]["ready"] is True


def test_seventy_nine_percent_is_unusable(tmp_path):
    missing = {"input_uncached_tokens": None, "cache_read_input_tokens": 1, "cache_creation_input_tokens": 1, "output_tokens": 1}
    items = [unit("p", i, f"T{i}") for i in range(1, 80)] + [unit("p", 80 + i, f"U{i}", usage=missing) for i in range(21)]
    make(tmp_path, {"p": items})
    doc = baseline.report(tmp_path, count=100)
    assert round(doc["coverage"]["input_uncached_tokens"]["coverage"], 2) == 0.79
    assert doc["coverage"]["input_uncached_tokens"]["usable"] is False
    assert any("Uncached input" in r for r in doc["readiness"]["reasons"])
    assert "Uncached input (unusable)" in baseline.markdown(doc)


def test_statistics_use_complete_turns_and_flag_spread(tmp_path):
    make(tmp_path, {"p": [unit("p", 1, "A"), unit("p", 2, "B", usage={"input_uncached_tokens": 300, "cache_read_input_tokens": 1000,
                                                                    "cache_creation_input_tokens": 10, "output_tokens": 50})]})
    doc = baseline.report(tmp_path, count=2)
    c = doc["categories"]["input_uncached_tokens"]
    assert (c["min"], c["max"], c["spread"], c["median"]) == (100, 300, 200, 200)
    assert c["flag"] is True and c["per_accepted"] == 200
    assert doc["categories"]["output_tokens"]["flag"] is False


def test_start_boundary_and_zero_acceptance(tmp_path):
    make(tmp_path, {"p": [unit("p", 1, "A"), unit("p", 2, "B", outcome="failed_runtime")]})
    doc = baseline.report(tmp_path, start="p/0002-task-t001-claude", count=1)
    assert [t["logical_turn_id"] for t in doc["turns"]] == ["B"]
    assert doc["categories"]["output_tokens"]["per_accepted"] is None
    assert "undefined" in baseline.markdown(doc)


def test_unlabeled_history_is_skipped_not_guessed(tmp_path):
    old = unit("p", 1, None)
    del old["logical_turn_id"]
    make(tmp_path, {"p": [old]})
    doc = baseline.report(tmp_path, count=1)
    assert doc["turns"] == [] and doc["skipped"]["unlabeled"] == 1


def test_cli_markdown_and_exit_code(tmp_path):
    make(tmp_path, {"p": [unit("p", i, f"T{i}") for i in range(1, 11)]})
    result = CliRunner().invoke(alfrd_cli, ["plan", "baseline", "-C", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "Readiness: **pass**" in result.output
    result = CliRunner().invoke(alfrd_cli, ["plan", "baseline", "-C", str(tmp_path), "--count", "11"])
    assert result.exit_code == 1
    result = CliRunner().invoke(alfrd_cli, ["plan", "baseline", "-C", str(tmp_path), "--exclude", "nope"])
    assert result.exit_code == 1 and "TURN_ID=evidence" in result.output
