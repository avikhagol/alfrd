import json
from types import SimpleNamespace

import pytest

from alfrd.agent_io import agent_command, elapsed_seconds, codex_model
from alfrd.model_prices import token_cost
from alfrd.runtime.scheduler import Runner


@pytest.mark.parametrize("agent", ["claude", "sonnet", "haiku", "codex", "luna", "agy"])
@pytest.mark.parametrize("status", ["done", "failed", "cancelled", "interrupted"])
def test_elapsed_is_alfrd_wall_time_for_every_outcome(agent, status):
    unit = {"agent": agent, "status": status, "started": "2026-10-01T10:00:00+02:00",
            "finished": "2026-10-01T08:00:12Z", "duration_ms": 999999, "usage": {"wall": 999999}}
    assert elapsed_seconds(unit) == 12
    # Adoption preserves the original start. Retries are separate attempts.
    assert elapsed_seconds({**unit, "retry_of": "old", "note": "finalized after a runner restart"}) == 12


@pytest.mark.parametrize("model", ["gpt-5.3-codex", "gpt-6.1-sol", "gpt-6-luna"])
def test_codex_cost_persisted_from_usage_even_on_failure(tmp_path, model):
    (tmp_path / "u.exit").write_text('{"exit_code": 1}')
    (tmp_path / "u.log").write_text(json.dumps({"type": "turn.completed", "usage": {
        "input_tokens": 1000, "cached_input_tokens": 400, "output_tokens": 100}}))
    unit = {"id": "u", "agent_step": True, "adapter": "codex", "requested_model": model,
            "exit_file": "u.exit", "log": "u.log", "status": "failed"}
    runner = Runner.__new__(Runner)
    runner.folder = SimpleNamespace(root=tmp_path, save_unit=lambda u: None)
    runner.agent_metadata(unit)
    assert unit["total_cost_usd"] > 0
    assert unit["cost_source"] == "api_estimate"
    assert unit["cost_reason"] is None
    assert json.loads((tmp_path / "u.agent.json").read_text())["total_cost_usd"] == unit["total_cost_usd"]
    runner.agent_metadata(unit)  # resumed/finalized twice: no duplicate charges
    assert unit["total_cost_usd"] == token_cost(model, unit["agent_usage"])[0]


def test_cached_tokens_are_not_double_charged_and_missing_reasons_are_specific():
    usage = {"input_tokens": 1000, "cache_read_input_tokens": 400, "output_tokens": 100}
    assert token_cost("gpt-5.3-codex", usage)[0] == pytest.approx(.00252)
    assert token_cost("gpt-5.3-codex", None) == (None, "Not reported by agent")
    assert token_cost("unknown", usage) == (None, "Unknown model price: unknown")
    assert "Incomplete" in token_cost("gpt-5.3-codex", {"total_tokens": 20})[1]
    assert token_cost("gpt-5.3-codex", dict.fromkeys(usage, 0)) == (0, None)


def test_codex_requests_structured_usage_without_changing_final_file():
    argv, _, _ = agent_command(["codex", "exec", "--output-last-message", "reply.md", "-"])
    assert argv[-1] == "-" and "--json" in argv
    assert argv.count("--output-last-message") == 1


def test_codex_model_snapshot_respects_configuration_and_cli(tmp_path):
    (tmp_path / "config.toml").write_text('model = "gpt-6.1-sol"\n[profiles.small]\nmodel = "gpt-6-luna"\n')
    env = {"CODEX_HOME": str(tmp_path)}
    assert codex_model(["codex", "exec"], env) == "gpt-6.1-sol"
    assert codex_model(["codex", "--profile", "small", "exec"], env) == "gpt-6-luna"
    assert codex_model(["codex", "exec", "-c", 'model="gpt-5.3-codex"'], env) == "gpt-5.3-codex"
    assert codex_model(["codex", "exec", "-m", "custom"], env) == "custom"
    assert codex_model(["codex", "exec"], {"CODEX_HOME": str(tmp_path / "missing")}) is None
