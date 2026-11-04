"""Small sanitized records matching Claude stream-json and Codex exec --json.

Claude shapes were checked against brainstorm .alfrd logs (20261024-225618).
Codex fixtures follow the item shapes documented in survey_backend.md; that
project currently contains only Claude raw logs. No transcript text is copied.
"""

import json

import pytest

from alfrd import telemetry


def write_log(tmp_path, events):
    path = tmp_path / "unit.events.jsonl"
    path.write_bytes(b"".join(json.dumps(e).encode() + b"\n" for e in events))
    return path


def claude_call(identifier, name, **args):
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": identifier, "name": name, "input": args}
    ]}}


def codex_item(identifier, kind, **fields):
    return {"type": "item.completed", "item": {
        "id": identifier, "type": kind, **fields,
    }}


def test_claude_dedupes_complete_blocks_and_ignores_partial_stream(tmp_path):
    call = claude_call("tool-1", "Bash", command="rtk alfrd mailbox read")
    error = {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "tool-1", "is_error": True,
         "content": "sanitized failure"}
    ]}}
    path = write_log(tmp_path, [
        {"type": "stream_event", "event": {"type": "content_block_start",
            "content_block": call["message"]["content"][0]}},
        call, call, error, error,
        {"type": "result", "uuid": "result-1"},
        {"type": "result", "uuid": "result-1"},
    ])
    result = telemetry.parse_claude(path)
    for key in ("tool_calls", "shell", "mailbox", "tool_errors", "turns"):
        assert result["counts"][key] == {"value": 1, "coverage": "complete"}
    assert result["counts"]["web_search"]["value"] == 0
    assert result["unknown"] == []
    assert result["parser_version"] == telemetry.PARSER_VERSION


def test_claude_classification_and_breakdown(tmp_path):
    events = [claude_call(str(i), name) for i, name in enumerate([
        "WebSearch", "WebFetch", "BashOutput", "Read", "Glob", "Grep",
        "NotebookRead", "Edit", "MultiEdit", "NotebookEdit", "Write", "Task",
        "Agent", "mcp__example__search", "ToolSearch",
    ])]
    server = claude_call("server-1", "web_search")
    server["message"]["content"][0]["type"] = "server_tool_use"
    events.append(server)
    counts = telemetry.parse_claude(write_log(tmp_path, events))["counts"]
    expected = {"tool_calls": 16, "web_search": 2, "web_fetch": 1, "shell": 1,
                "file_read": 4, "file_edit": 4, "subagent": 2, "mcp": 1}
    for key, value in expected.items():
        assert counts[key] == {"value": value, "coverage": "complete"}
    assert counts["files_changed"]["coverage"] == "unavailable"


def test_codex_completed_only_dedupe_errors_and_file_count(tmp_path):
    command = codex_item("c1", "command_execution", command="alfrd mailbox read",
                         status="failed", exit_code=2)
    events = [
        {"type": "item.started", "item": command["item"]}, command, command,
        codex_item("s1", "web_search", query="sanitized"),
        codex_item("f1", "file_change", changes=[{"path": "a.py"}, {"path": "b.py"}]),
        codex_item("m1", "mcp_tool_call", error={"message": "sanitized"}),
        codex_item("c2", "command_execution", command="true", exit_code=0),
        *[codex_item(str(i), name) for i, name in enumerate(
            ["agent_message", "reasoning", "todo_list"])],
        {"type": "turn.completed", "usage": {"input_tokens": 1}},
    ]
    result = telemetry.parse_codex(write_log(tmp_path, events))
    for key, value in {"tool_calls": 5, "shell": 2, "web_search": 1,
                       "mailbox": 1, "file_edit": 1, "files_changed": 2,
                       "mcp": 1, "tool_errors": 2, "turns": 1}.items():
        assert result["counts"][key] == {"value": value, "coverage": "complete"}
    assert result["counts"]["file_read"]["coverage"] == "unavailable"


@pytest.mark.parametrize("adapter,event", [
    ("claude", claude_call("u1", "unfamiliar_search_tool")),
    ("codex", codex_item("u1", "future_tool")),
])
def test_unknown_tools_are_calls_not_guessed_searches(tmp_path, adapter, event):
    result = telemetry.parse_log(write_log(tmp_path, [event, event]), adapter)
    assert len(result["unknown"]) == 1
    assert result["counts"]["tool_calls"] == {"value": 1, "coverage": "complete"}
    assert result["counts"]["web_search"]["value"] is None
    assert result["counts"]["web_search"]["coverage"] == "partial"
    assert result["coverage"] == "partial"


@pytest.mark.parametrize("adapter", ["generic", "agy", "llm-chat"])
def test_generic_unavailable_even_with_recognizable_events(tmp_path, adapter):
    result = telemetry.parse_log(write_log(tmp_path, [claude_call("1", "Bash")]), adapter)
    assert result["source"] == result["coverage"] == "unavailable"
    assert all(c["value"] is None and c["coverage"] == "unavailable"
               for c in result["counts"].values())


def test_oversize_line_sniffed_not_parsed_and_next_line_survives(tmp_path, monkeypatch):
    path = tmp_path / "unit.events.jsonl"
    large = json.dumps(claude_call("huge", "Bash", command="x" * telemetry.MAX_LINE_BYTES)).encode()
    normal = json.dumps(claude_call("small", "WebSearch")).encode()
    path.write_bytes(large + b"\n" + normal + b"\n")
    original = telemetry.json.loads
    def guarded(line):
        assert len(line) <= telemetry.MAX_LINE_BYTES
        return original(line)
    monkeypatch.setattr(telemetry.json, "loads", guarded)
    result = telemetry.parse_claude(path)
    assert result["oversize_lines"] == 1
    assert result["oversize_sniffs"][0]["id"] == "huge"
    assert result["counts"]["tool_calls"]["value"] == 1
    assert result["counts"]["tool_calls"]["coverage"] == "partial"
    assert result["counts"]["web_search"]["value"] == 1
    assert result["counts"]["shell"]["value"] is None


def test_line_limit_is_bytes_and_exact_limit_is_parseable(tmp_path):
    event = json.dumps(claude_call("a", "Read")).encode()
    path = tmp_path / "unit.events.jsonl"
    path.write_bytes(event + b" " * (telemetry.MAX_LINE_BYTES - len(event) - 1) + b"\n")
    assert telemetry.parse_claude(path)["counts"]["file_read"]["value"] == 1
    path.write_bytes(event + ("é" * telemetry.MAX_LINE_BYTES).encode() + b"\n")
    assert telemetry.parse_claude(path)["oversize_lines"] == 1


def test_corrupt_and_partial_lines_keep_lower_bounds(tmp_path):
    path = write_log(tmp_path, [claude_call("a", "Read")])
    with path.open("ab") as stream:
        stream.write(b'invalid\n\xff\n{"type": "assistant"')
    result = telemetry.parse_claude(path)
    assert result["counts"]["file_read"]["value"] == 1
    assert result["counts"]["file_read"]["coverage"] == "partial"
    assert result["partial_tail"] is True


def test_complete_last_line_without_newline(tmp_path):
    path = tmp_path / "unit.events.jsonl"
    path.write_text(json.dumps(codex_item("a", "web_search")))
    result = telemetry.parse_codex(path)
    assert result["counts"]["web_search"] == {"value": 1, "coverage": "complete"}
    assert result["partial_tail"] is False


def test_missing_log_unavailable_empty_log_complete(tmp_path):
    path = tmp_path / "unit.events.jsonl"
    assert telemetry.parse_claude(path)["counts"]["shell"]["coverage"] == "unavailable"
    path.touch()
    assert telemetry.parse_claude(path)["counts"]["shell"] == {"value": 0, "coverage": "complete"}


def test_malformed_shapes_and_missing_ids_are_not_counted(tmp_path):
    events = [None, [], {"type": []}, claude_call(None, "Bash"),
              {"type": "assistant", "message": {"content": [None, {"type": []}]}}]
    result = telemetry.parse_claude(write_log(tmp_path, events))
    assert result["counts"]["tool_calls"]["value"] is None
    assert result["counts"]["tool_calls"]["coverage"] == "partial"


def test_oversize_eof_diagnostics_bounded(tmp_path):
    path = tmp_path / "unit.events.jsonl"
    path.write_bytes(b'{"type":"assistant","id":"large","data":"' +
                     b"x" * (telemetry.MAX_LINE_BYTES * 3))
    result = telemetry.parse_claude(path)
    assert result["oversize_lines"] == 1
    assert result["partial_tail"] is True
    assert len(json.dumps(result)) < 5000


@pytest.mark.parametrize("command,expected", [
    ("alfrd mailbox read", 1), ("rtk proxy alfrd mailbox read", 1),
    ("echo alfrd mailbox read", 0), ("alfrd mailbox-other", 0),
])
def test_mailbox_subset_matches_command_prefix(tmp_path, command, expected):
    result = telemetry.parse_codex(write_log(tmp_path, [
        codex_item("a", "command_execution", command=command),
    ]))
    assert result["counts"]["mailbox"]["value"] == expected
    assert result["counts"]["tool_calls"]["value"] == 1
    assert result["counts"]["shell"]["value"] == 1
