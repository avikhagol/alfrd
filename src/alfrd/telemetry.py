"""Token-free counters for per-unit provider ``.events.jsonl`` logs.

``parse_log(path, adapter)`` returns JSON-serializable counts. Categories are
subsets of tool_calls, not additional calls. Shell contents are never interpreted
as reads/edits/searches. Unknown tools still count as calls, but make category
coverage partial. No provider calls are made here. ``advance`` keeps an incremental
``alfrd.telemetry/1`` sidecar next to the log; the raw log is only ever read.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Literal, TypedDict

PARSER_VERSION = "1"
MAX_LINE_BYTES = 1024 * 1024
READ_CHUNK_BYTES = 64 * 1024
SNIFF_BYTES = 4096
ADVANCE_BYTES = 4 * 1024 * 1024
SCHEMA = "alfrd.telemetry/1"


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

    _SETS = ("unknown", "seen_calls", "seen_errors", "seen_turns", "reasons", "category_reasons")

    def state(self) -> dict:
        """JSON-serializable dedupe/count state, restorable with ``restore``."""
        out = {k: sorted(getattr(self, k)) for k in self._SETS}
        out.update(values=dict(self.values), oversize_lines=self.oversize_lines,
                   oversize_sniffs=list(self.oversize_sniffs), partial_tail=self.partial_tail)
        return out

    def restore(self, state: dict) -> None:
        for key in self._SETS:
            setattr(self, key, {x for x in state.get(key, []) if isinstance(x, str)})
        values = state.get("values") or {}
        self.values = {k: int(values.get(k) or 0) for k in COUNTERS}
        self.oversize_lines = int(state.get("oversize_lines") or 0)
        self.oversize_sniffs = [d for d in state.get("oversize_sniffs", []) if isinstance(d, dict)][:20]
        self.partial_tail = bool(state.get("partial_tail"))

    def result(self, extra_reason: str | None = None) -> dict:
        if extra_reason:
            self.reasons.add(extra_reason)
        try:
            return self._result()
        finally:
            if extra_reason:
                self.reasons.discard(extra_reason)

    def _result(self) -> dict:
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


def sidecar_path(log: str | Path) -> Path:
    return Path(log).with_suffix(".telemetry.json")


def _load_sidecar(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    if not isinstance(data, dict) or data.get("schema") != SCHEMA or not isinstance(data.get("parser_state"), dict):
        return None
    return data


def _write_sidecar(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def advance(log: str | Path, adapter: str, *, budget: int = ADVANCE_BYTES, finalize: bool = False,
            write: bool = True) -> dict:
    """Advance the sidecar of one ``.events.jsonl`` by at most ``budget`` bytes (64 KiB reads).

    A changed inode, a shrunken file, a different parser version or adapter rebuilds from
    offset 0. The offset only moves past complete lines (or discarded oversize bytes), so a
    partial tail is re-read next time; ``finalize`` resolves it at unit exit and keeps
    reading to EOF. The raw log is opened read-only. Returns the sidecar document."""
    log = Path(log)
    side = sidecar_path(log)
    parser = TelemetryParser(adapter)
    try:
        stat = log.stat()
    except OSError:
        doc = parse_log(log, adapter)
        return {"schema": SCHEMA, **doc, "inode": None, "size": 0, "offset": 0, "behind": False}
    prior = _load_sidecar(side)
    offset, skipping, rebuilt = 0, False, True
    if (prior and prior.get("parser_version") == PARSER_VERSION and prior.get("adapter") == adapter
            and prior.get("inode") == stat.st_ino and isinstance(prior.get("size"), int)
            and prior["size"] <= stat.st_size and isinstance(prior.get("offset"), int)
            and 0 <= prior["offset"] <= stat.st_size):
        if prior.get("finalized"):
            if prior["size"] == stat.st_size:
                return prior
        else:
            rebuilt = False
    if not rebuilt:
        offset, skipping = prior["offset"], bool(prior.get("skipping"))
        parser.restore(prior["parser_state"])
    if not finalize and not rebuilt and prior and prior["size"] == stat.st_size and prior["offset"] == stat.st_size:
        return prior
    # A call must see a whole maximal line (plus one chunk) to either complete it or mark it
    # oversize; a smaller budget would re-read the same unfinished line forever.
    limit = None if finalize else max(MAX_LINE_BYTES + READ_CHUNK_BYTES, int(budget))
    read_total, buffer = 0, b""
    try:
        with log.open("rb") as stream:
            stream.seek(offset)
            while limit is None or read_total < limit:
                chunk = stream.read(READ_CHUNK_BYTES if limit is None else min(READ_CHUNK_BYTES, limit - read_total))
                if not chunk:
                    break
                read_total += len(chunk)
                buffer += chunk
                pos = 0  # consumed prefix of buffer; sliced once per chunk, not per line
                while True:
                    nl = buffer.find(b"\n", pos)
                    if skipping:
                        if nl < 0:
                            offset += len(buffer) - pos
                            pos = len(buffer)
                            break
                        offset += nl + 1 - pos
                        pos = nl + 1
                        skipping = False
                    elif nl >= 0 and nl - pos < MAX_LINE_BYTES:
                        parser.feed_line(buffer[pos:nl + 1])
                        offset += nl + 1 - pos
                        pos = nl + 1
                    elif len(buffer) - pos > MAX_LINE_BYTES:
                        parser.oversize(buffer[pos:pos + SNIFF_BYTES])
                        skipping = True
                    else:
                        break
                buffer = buffer[pos:]
            size_now = stream.seek(0, os.SEEK_END)
    except OSError:
        doc = parse_log(log, adapter)
        return {"schema": SCHEMA, **doc, "inode": stat.st_ino, "size": stat.st_size, "offset": 0, "behind": False}
    if finalize and buffer and not skipping and offset + len(buffer) >= size_now:
        try:
            json.loads(buffer)
        except (ValueError, UnicodeError, RecursionError):
            parser.partial_tail = True
            parser.reasons.add("incomplete final line")
        else:
            parser.feed_line(buffer)
        offset += len(buffer)
    elif finalize and skipping:
        parser.partial_tail = True
    size = max(stat.st_size, size_now)
    behind = offset < size and not finalize
    doc = parser.result("log not fully scanned" if behind else None)
    doc.update(schema=SCHEMA, inode=stat.st_ino, size=size, offset=offset, behind=behind,
               skipping=skipping, finalized=bool(finalize), parser_state=parser.state(),
               rebuilt=rebuilt)
    if write:
        _write_sidecar(side, doc)
    return doc


def running(doc: Mapping[str, Any]) -> dict[str, Any]:
    """A running attempt's document: it still grows, so each complete count is only a lower bound (partial)."""
    def soften(coverage: object) -> object:
        return "partial" if coverage == "complete" else coverage

    counts = {key: {**count, "coverage": soften(count.get("coverage"))} for key, count in (doc.get("counts") or {}).items()}
    return {**doc, "live": True, "coverage": soften(doc.get("coverage")), "counts": counts}


def _count_of(doc: Mapping[str, Any] | None, key: str) -> tuple[int | None, str]:
    """One attempt's (value, coverage) for a counter. A live attempt is never complete: it still grows."""
    count = ((doc or {}).get("counts") or {}).get(key)
    count = count if isinstance(count, dict) else {}
    value = count.get("value")
    if isinstance(value, bool) or not isinstance(value, int):
        value = None
    coverage = count.get("coverage")
    if coverage not in ("complete", "partial", "unavailable"):
        coverage = "unavailable"
    if coverage == "complete" and (value is None or (doc or {}).get("live")):
        coverage = "partial"
    if coverage == "unavailable":
        return None, "unavailable"
    return value, coverage


def totals(attempts: Iterable[Mapping[str, Any] | None]) -> dict[str, Any]:
    """Sum attempt documents (``advance()`` results; None when an attempt has no sidecar) into one tally.

    Each attempt must be passed once; the caller owns that. Per counter, ``coverage`` is ``complete`` only when
    every attempt is complete, ``unavailable`` when no attempt reports it, and ``partial`` otherwise. A partial
    sum is a lower bound ("At least N"). A partial zero says nothing, so it stays ``value: None``: 0 only with
    complete coverage.
    """
    docs = list(attempts)
    counts: dict[str, dict[str, Any]] = {}
    for key in COUNTERS:
        states = [_count_of(doc, key) for doc in docs]
        coverages = [coverage for _, coverage in states]
        values = [value for value, _ in states if value is not None]
        total = sum(values) if values else None
        if all(coverage == "unavailable" for coverage in coverages):
            counts[key] = {"value": None, "coverage": "unavailable"}
        elif all(coverage == "complete" for coverage in coverages):
            counts[key] = {"value": total, "coverage": "complete"}
        else:
            counts[key] = {"value": total or None, "coverage": "partial"}
    unknown = {name for doc in docs if doc for name in doc.get("unknown") or [] if isinstance(name, str)}
    return {"attempts": len(docs), "counts": counts, "unknown": sorted(unknown),
            "oversize_lines": sum(int(doc.get("oversize_lines") or 0) for doc in docs if doc)}
