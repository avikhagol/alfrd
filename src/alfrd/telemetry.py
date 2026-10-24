"""Token-free counters for per-unit provider ``.events.jsonl`` logs.

``parse_log(path, adapter)`` returns JSON-serializable counts. Categories are
subsets of tool_calls, not additional calls. Shell contents are never interpreted
as reads/edits/searches. Unknown tools still count as calls, but make category
coverage partial. No cache/sidecar or provider calls are made here.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal, TypedDict

PARSER_VERSION = "1"
MAX_LINE_BYTES = 1024 * 1024
READ_CHUNK_BYTES = 64 * 1024
SNIFF_BYTES = 4096


class Count(TypedDict, total=False):
    value: int | None
    coverage: Literal["complete", "partial", "unavailable"]
    reason: str


COUNTERS = (
    "tool_calls", "web_search", "shell", "file_edit", "tool_errors",
    "web_fetch", "file_read", "subagent", "mcp", "mailbox", "turns",
    "files_changed",
)

# Exact names only: a tool containing 'search' need not search the web.
# MCP's prefix and the command-based mailbox subset are handled separately.
CLASSIFY = {
    "claude": {
        "WebSearch": "web_search", "web_search": "web_search",
        "WebFetch": "web_fetch", "Bash": "shell", "BashOutput": "shell",
        "Read": "file_read", "Glob": "file_read", "Grep": "file_read",
        "NotebookRead": "file_read", "Edit": "file_edit",
        "MultiEdit": "file_edit", "NotebookEdit": "file_edit",
        "Write": "file_edit", "Task": "subagent", "Agent": "subagent",
        "ToolSearch": None, "TaskStop": None, "TodoWrite": None,
        "ExitPlanMode": None, "EnterPlanMode": None, "AskUserQuestion": None,
    },
    "codex": {
        "web_search": "web_search", "command_execution": "shell",
        "file_change": "file_edit", "mcp_tool_call": "mcp",
    },
}
_NON_TOOL_ITEMS = {"agent_message", "reasoning", "todo_list"}
_MAILBOX = re.compile(r"^\s*(?:rtk\s+(?:proxy\s+)?)?alfrd\s+mailbox(?:\s|$)")
_SNIFF = re.compile(rb'"(type|id|name|tool_use_id)"\s*:\s*"([^"\\\r\n]{1,128})"')


class TelemetryParser:
    """In-memory dedupe state; feed complete bounded lines, then call result().

    Oversize prefixes are diagnostic only: a truncated/nested JSON object cannot
    prove a completed tool call. Skipping them leaves honest lower bounds.
    Instances belong to one unit/attempt; never share IDs across attempts.
    """

    def __init__(self, adapter: str):
        self.adapter = adapter
        self.values = dict.fromkeys(COUNTERS, 0)
        self.unknown: set[str] = set()
        self.seen_calls: set[str] = set()
        self.seen_errors: set[str] = set()
        self.seen_turns: set[str] = set()
        self.reasons: set[str] = set()
        self.category_reasons: set[str] = set()
        self.oversize_lines = 0
        self.oversize_sniffs: list[dict[str, str]] = []
        self.partial_tail = False

    def oversize(self, prefix: bytes) -> None:
        """Sniff at most 4 KiB, without JSON parsing or retaining the payload."""
        self.oversize_lines += 1
        self.reasons.add("oversize lines skipped")
        # Keep diagnostics bounded even for logs containing many huge records.
        if len(self.oversize_sniffs) < 20:
            self.oversize_sniffs.append({
                k.decode(): v.decode("utf-8", "replace")
                for k, v in _SNIFF.findall(prefix[:SNIFF_BYTES])
            })

    def feed_line(self, line: bytes | str) -> None:
        if isinstance(line, str):
            line = line.encode("utf-8")
        if len(line) > MAX_LINE_BYTES:
            self.oversize(line)
            return
        if self.adapter not in CLASSIFY or not line.strip():
            return
        try:
            event = json.loads(line)
        except (ValueError, UnicodeError, RecursionError):
            self.reasons.add("invalid JSON lines skipped")
            return
        if not isinstance(event, dict):
            self.reasons.add("non-object events skipped")
            return
        if not isinstance(event.get("type"), str):
            self.reasons.add("missing event types")
            return
        if self.adapter == "claude":
            self._claude(event)
        else:
            self._codex(event)

    def _once(self, identifier: object, seen: set[str]) -> bool:
        if not isinstance(identifier, str) or not identifier:
            self.reasons.add("missing event identifiers")
            return False
        if identifier in seen:
            return False
        seen.add(identifier)
        return True

    def _call(self, name: object, command: object = None) -> None:
        self.values["tool_calls"] += 1
        if not isinstance(name, str) or not name:
            name = "<missing>"
        table = CLASSIFY[self.adapter]
        if self.adapter == "claude" and name.startswith("mcp__"):
            category = "mcp"
        elif name in table:
            category = table[name]
        else:
            self.unknown.add(name)
            self.category_reasons.add("unknown tool names")
            category = None
        if category:
            self.values[category] += 1
        if category == "shell" and isinstance(command, str) and _MAILBOX.match(command):
            self.values["mailbox"] += 1

    def _claude(self, event: dict) -> None:
        kind = event.get("type")
        # Never count stream_event.content_block_start/delta copies.
        if kind == "result":
            if self._once(event.get("uuid"), self.seen_turns):
                self.values["turns"] += 1
        elif kind in {"assistant", "user"}:
            message = event.get("message")
            blocks = message.get("content") if isinstance(message, dict) else None
            if not isinstance(blocks, list):
                return  # user text may be a string
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type")
                if kind == "assistant" and block_type in ("tool_use", "server_tool_use"):
                    if self._once(block.get("id"), self.seen_calls):
                        args = block.get("input")
                        command = args.get("command") if isinstance(args, dict) else None
                        self._call(block.get("name"), command)
                elif block_type == "tool_result" and block.get("is_error") is True:
                    if self._once(block.get("tool_use_id"), self.seen_errors):
                        self.values["tool_errors"] += 1

    def _codex(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "turn.completed":
            # The CLI does not provide a turn ID; each completion is one turn.
            self.values["turns"] += 1
        elif kind == "item.completed":
            item = event.get("item")
            if not isinstance(item, dict):
                self.reasons.add("missing completed item")
                return
            name = item.get("type")
            if isinstance(name, str) and name in _NON_TOOL_ITEMS:
                return
            if not self._once(item.get("id"), self.seen_calls):
                return
            self._call(name, item.get("command"))
            if item.get("status") == "failed" or (
                isinstance(item.get("exit_code"), int) and item["exit_code"] != 0
            ) or item.get("is_error") is True or item.get("error") is not None:
                self.values["tool_errors"] += 1
            if name == "file_change":
                changes = item.get("changes")
                if isinstance(changes, list):
                    self.values["files_changed"] += len(changes)
                else:
                    self.category_reasons.add("file_change missing changes")

    def result(self) -> dict:
        supported = self.adapter in CLASSIFY
        counts: dict[str, Count] = {}
        for key, value in self.values.items():
            unavailable = (
                self.adapter == "claude" and key == "files_changed"
            ) or (
                self.adapter == "codex" and key in {"web_fetch", "file_read", "subagent"}
            )
            if not supported or unavailable:
                counts[key] = {"value": None, "coverage": "unavailable",
                               "reason": "adapter does not report this counter"}
                continue
            reasons = self.reasons.copy()
            if key not in {"tool_calls", "tool_errors", "turns"}:
                reasons.update(self.category_reasons)
            if reasons:
                counts[key] = {"value": value or None, "coverage": "partial",
                               "reason": "; ".join(sorted(reasons))}
            else:
                counts[key] = {"value": value, "coverage": "complete"}
        coverage = "unavailable" if not supported else (
            "partial" if self.reasons or self.category_reasons else "complete")
        return {"adapter": self.adapter, "parser_version": PARSER_VERSION,
                "source": "parsed" if supported else "unavailable",
                "counts": counts, "unknown": sorted(self.unknown),
                "oversize_lines": self.oversize_lines,
                "oversize_sniffs": list(self.oversize_sniffs),
                "partial_tail": self.partial_tail, "coverage": coverage}


def parse_log(path: str | Path, adapter: str) -> dict:
    """Read one unit log with bounded allocations, including oversize lines.

    Missing/unreadable logs report unavailable. An incomplete final line is
    skipped and marked partial; a valid JSON record needs no terminal newline.
    """
    parser = TelemetryParser(adapter)
    if adapter not in CLASSIFY:
        return parser.result()
    try:
        with Path(path).open("rb") as stream:
            while True:
                line = stream.readline(MAX_LINE_BYTES + 1)
                if not line:
                    break
                if len(line) > MAX_LINE_BYTES:
                    parser.oversize(line[:SNIFF_BYTES])
                    while not line.endswith(b"\n"):
                        line = stream.readline(READ_CHUNK_BYTES)
                        if not line:
                            parser.partial_tail = True
                            break
                    continue
                if not line.endswith(b"\n"):
                    try:
                        json.loads(line)
                    except (ValueError, UnicodeError, RecursionError):
                        parser.partial_tail = True
                        parser.reasons.add("incomplete final line")
                        break
                parser.feed_line(line)
    except OSError:
        result = parser.result()
        result["coverage"] = result["source"] = "unavailable"
        result["counts"] = {
            key: {"value": None, "coverage": "unavailable", "reason": "log unreadable"}
            for key in COUNTERS
        }
        return result
    return parser.result()


def parse_claude(path: str | Path) -> dict:
    return parse_log(path, "claude")


def parse_codex(path: str | Path) -> dict:
    return parse_log(path, "codex")
