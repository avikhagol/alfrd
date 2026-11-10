"""Golden checks for telemetry parsers on sanitized transcripts.

Real fixtures come from brainstorm .alfrd unit logs, with prose, thinking,
tool output, paths, emails and credential-looking strings removed. The
retry, unknown and partial fixtures keep real Claude record shapes but are
written by hand. Golden JSON lives under fixtures/telemetry/golden/.
"""

import json
import re
from pathlib import Path

import pytest

from alfrd import telemetry

FIXTURES = Path(__file__).parent / "fixtures" / "telemetry"
GOLDEN = FIXTURES / "golden"
GOLDEN_NAMES = [
    "claude_branch_haiku", "claude_session_t1", "claude_retry_errors",
    "claude_unknown_events", "claude_partial_tail",
]


def fixture(name):
    return FIXTURES / f"{name}.events.jsonl"


def golden(name):
    return json.loads((GOLDEN / f"{name}.json").read_text())


@pytest.mark.parametrize("name", GOLDEN_NAMES)
def test_fixture_matches_golden_output(name):
    assert telemetry.parse_claude(fixture(name)) == golden(name)


@pytest.mark.parametrize("name", GOLDEN_NAMES)
def test_golden_output_is_json_serializable_and_versioned(name):
    result = telemetry.parse_log(str(fixture(name)), "claude")
    assert json.loads(json.dumps(result)) == result
    assert result["parser_version"] == telemetry.PARSER_VERSION


def test_real_transcript_stream_snapshots_are_not_double_counted():
    lines = fixture("claude_branch_haiku").read_text().splitlines()
    starts = [line for line in lines if '"content_block_start"' in line]
    assert len(starts) == 7
    result = telemetry.parse_claude(fixture("claude_branch_haiku"))
    assert result["counts"]["tool_calls"] == {"value": 7, "coverage": "complete"}
    assert result["counts"]["turns"] == {"value": 1, "coverage": "complete"}
    assert result["coverage"] == "complete"


def test_real_session_breakdown_matches_transcript():
    result = telemetry.parse_claude(fixture("claude_session_t1"))
    assert result["counts"]["tool_calls"]["value"] == 12
    assert result["counts"]["shell"]["value"] == 11
    assert result["counts"]["file_read"]["value"] == 1
    assert result["counts"]["mailbox"]["value"] == 1
    assert result["counts"]["files_changed"]["coverage"] == "unavailable"


def test_retries_count_each_call_and_error_once():
    result = telemetry.parse_claude(fixture("claude_retry_errors"))
    # Three attempts are three calls. Two failed attempts are two errors.
    # The duplicated assistant and tool_result records are ignored.
    assert result["counts"]["tool_calls"] == {"value": 3, "coverage": "complete"}
    assert result["counts"]["mailbox"] == {"value": 3, "coverage": "complete"}
    assert result["counts"]["tool_errors"] == {"value": 2, "coverage": "complete"}
    assert result["counts"]["turns"] == {"value": 1, "coverage": "complete"}
    assert result["coverage"] == "complete"


def test_unknown_events_and_tools_lower_coverage_without_guessing():
    result = telemetry.parse_claude(fixture("claude_unknown_events"))
    assert result["unknown"] == ["FutureSearchTool"]
    # Unknown event types are ignored silently; malformed records are not.
    assert result["counts"]["tool_calls"]["value"] == 2
    assert result["counts"]["tool_calls"]["coverage"] == "partial"
    assert result["counts"]["web_search"] == {
        "value": None, "coverage": "partial",
        "reason": result["counts"]["web_search"]["reason"],
    }
    assert "unknown tool names" in result["counts"]["web_search"]["reason"]
    assert result["counts"]["mcp"] == {
        "value": 1, "coverage": "partial",
        "reason": result["counts"]["mcp"]["reason"],
    }
    reason = result["counts"]["tool_calls"]["reason"]
    for expected in ("invalid JSON lines skipped", "non-object events skipped",
                     "missing event identifiers"):
        assert expected in reason
    assert result["coverage"] == "partial"


def test_partial_tail_is_skipped_and_reported():
    result = telemetry.parse_claude(fixture("claude_partial_tail"))
    assert result["partial_tail"] is True
    assert result["coverage"] == "partial"
    assert result["counts"]["tool_calls"]["value"] == 2
    assert result["counts"]["tool_calls"]["coverage"] == "partial"
    assert "incomplete final line" in result["counts"]["tool_calls"]["reason"]
    assert result["counts"]["mailbox"]["value"] == 1
    assert result["counts"]["file_read"]["value"] == 1


def test_oversize_real_shaped_call_is_sniffed_not_parsed(tmp_path):
    padding = "x" * telemetry.MAX_LINE_BYTES
    oversize = {"type": "assistant", "uuid": "uuid", "message": {
        "role": "assistant", "content": [{
            "type": "tool_use", "id": "toolu_big", "name": "Bash",
            "input": {"command": "alfrd mailbox read " + padding},
        }]}}
    normal = {"type": "assistant", "uuid": "uuid", "message": {
        "role": "assistant", "content": [{
            "type": "tool_use", "id": "toolu_small", "name": "Bash",
            "input": {"command": "alfrd mailbox read"},
        }]}}
    path = tmp_path / "unit.events.jsonl"
    path.write_bytes(json.dumps(oversize).encode() + b"\n" +
                     json.dumps(normal).encode() + b"\n")
    result = telemetry.parse_claude(path)
    assert result["oversize_lines"] == 1
    assert result["oversize_sniffs"][0]["id"] == "toolu_big"
    assert "xxxx" not in json.dumps(result)
    assert result["counts"]["tool_calls"] == {
        "value": 1, "coverage": "partial",
        "reason": "oversize lines skipped",
    }
    assert result["counts"]["mailbox"]["value"] == 1
    assert result["counts"]["shell"]["value"] == 1


def test_oversize_error_is_skipped_and_next_error_still_counted(tmp_path):
    error = {"type": "user", "message": {"role": "user", "content": [{
        "type": "tool_result", "tool_use_id": "toolu_big", "is_error": True,
        "content": "x" * (telemetry.MAX_LINE_BYTES + 10),
    }]}}
    retry = {"type": "user", "message": {"role": "user", "content": [{
        "type": "tool_result", "tool_use_id": "toolu_small", "is_error": True,
        "content": "sanitized output",
    }]}}
    path = tmp_path / "unit.events.jsonl"
    path.write_bytes(json.dumps(error).encode() + b"\n" +
                     json.dumps(retry).encode() + b"\n")
    result = telemetry.parse_claude(path)
    assert result["oversize_lines"] == 1
    assert result["counts"]["tool_errors"]["value"] == 1
    assert result["counts"]["tool_errors"]["coverage"] == "partial"


def test_fixtures_contain_no_paths_emails_or_credentials():
    email = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
    secret = re.compile(
        r"sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{12,}|AKIA[0-9A-Z]{12,}"
        r"|[A-Fa-f0-9]{32,}|(?i:bearer)\s+[A-Za-z0-9]"
    )
    files = [p for p in FIXTURES.rglob("*") if p.is_file()]
    assert len(files) == len(GOLDEN_NAMES) * 2
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert "/mnt/" not in text and "/home/" not in text, path
        assert "akumar" not in text and "/avi" not in text, path
        assert set(email.findall(text)) <= {"user@example.com"}, path
        assert not secret.search(text), path
