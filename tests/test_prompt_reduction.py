from pathlib import Path
from types import SimpleNamespace

import pytest

from alfrd.agent_loop import compact_input, prepare, resolve_handoff


def step(iteration, **options):
    return SimpleNamespace(handoff={"input": "{target}/next-step-claude.md", "output": "{target}/next-step-codex.md"},
        entrypoint="claude", base_step="claude-turn", id="turn", iteration=iteration,
        iterations=3, final_turn=False, first_turn=True, next_agent="codex", next_roles=(),
        roles=({"label": "Developer", "instructions": "Implement carefully. " + "Detailed responsibilities. " * 100,
                "summary": "Implement and verify."},), loop_options=options)


def test_first_turn_persona_then_summary_and_metadata_removed(tmp_path):
    folder = tmp_path / "fix-login"
    folder.mkdir()
    (folder / "next-step-claude.md").write_text("<!-- ALFRD plan=old unit=old -->\n## Goal\nFix login.\n")
    first = prepare(tmp_path, tmp_path / "first", step(1), "p", "u", target="fix-login")
    later = prepare(tmp_path, tmp_path / "later", step(2), "p", "u", target="fix-login")
    initial = Path(first["prompt_file"]).read_text()
    prompt = Path(later["prompt_file"]).read_text()
    assert "Detailed responsibilities." in initial
    assert "Detailed responsibilities." not in prompt
    assert "- Developer: Implement and verify." in prompt
    assert "<!-- ALFRD" not in prompt
    assert later["prompt_chars"] == len(prompt) < first["prompt_chars"]


def test_summary_fallback_first_sentence(tmp_path):
    (tmp_path / "task").mkdir()
    (tmp_path / "task/next-step-claude.md").write_text("Task")
    spec = step(2)
    spec.roles = ({"label": "Dev", "instructions": "Implement carefully. Detailed second sentence."},)
    record = prepare(tmp_path, tmp_path / "archive", spec, "p", "u")
    prompt = Path(record["prompt_file"]).read_text()
    assert "- Dev: Implement carefully." in prompt
    assert "Detailed second sentence." not in prompt


def test_compact_input_preserves_action_sections_and_archive(tmp_path):
    kept = [f"## {heading}\nRequired {heading}.\n" for heading in
            ("Goal", "Instructions for the next agent", "Acceptance criteria", "Blockers")]
    text = kept[0] + "## What was completed\n" + "x" * 50000 + "\n" + "".join(kept[1:])
    archive = tmp_path / "input.md"
    result = compact_input(text, 4000, archive)
    assert len(result) <= 4000
    assert all(part in result for part in kept)
    assert "…[truncated by ALFRD; full text in " in result
    assert archive.read_text() == text


def test_required_sections_too_large_fail_without_silent_loss(tmp_path):
    with pytest.raises(ValueError, match="preserve"):
        compact_input("## Goal\n" + "x" * 1000, 100, tmp_path / "input.md")


@pytest.mark.parametrize("target", ["../escape", ".hidden", "a/b", "", "x" * 65])
def test_target_path_rejects_escape(tmp_path, target):
    with pytest.raises(ValueError):
        resolve_handoff(tmp_path, "{target}/next-step-claude.md", target)


def test_legacy_root_handoff(tmp_path):
    assert resolve_handoff(tmp_path, "next-step-claude.md", "task") == tmp_path / "next-step-claude.md"
