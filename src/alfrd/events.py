"""Structured plan events: ``.alfrd/plans/<id>/events.jsonl``.

The runner appends one JSON object per line, each with a ``seq`` that counts up
per plan (also across runner restarts and rotation) and a ``kind`` from
:data:`EVENT_KINDS`. Notifiers and the Studio read these lines; the plan's
free-text ``history`` stays as it was.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

EVENT_KINDS: frozenset[str] = frozenset({
    "plan.started", "plan.finished", "plan.failed", "plan.cancelled", "plan.interrupted",
    "turn.started", "turn.finished", "turn.failed", "turn.retrying", "turn.fallback_model", "turn.idle",
    "review.pending", "review.approved", "review.rejected",
    "handoff.published", "limit.reached",
})

EVENTS_FILE = "events.jsonl"
MAX_BYTES = 10 * 1024 * 1024
READ_LIMIT = 500


def kind_matches(pattern: str, kind: str) -> bool:
    """True for an exact kind, a ``prefix.*`` pattern covering it, or ``*``."""
    pattern = str(pattern).strip()
    if pattern == "*":
        return True
    if pattern.endswith(".*"):
        return kind.startswith(pattern[:-1])
    return pattern == kind


def _last_seq(path: Path) -> int | None:
    """The ``seq`` of the last complete line of ``path`` (a torn final line is skipped)."""
    try:
        with open(path, "rb") as fh:
            end = fh.seek(0, os.SEEK_END)
            chunk = 65536
            pos, tail = end, b""
            while pos > 0:
                step = min(chunk, pos)
                pos -= step
                fh.seek(pos)
                tail = fh.read(step) + tail
                lines = tail.splitlines()
                if pos > 0:
                    lines = lines[1:]  # may start mid-line
                for line in reversed(lines):
                    try:
                        seq = json.loads(line).get("seq")
                    except (ValueError, AttributeError):
                        continue
                    if isinstance(seq, int):
                        return seq
                if len(tail) > 4 * chunk:  # nothing parseable near the end
                    break
    except OSError:
        return None
    return None


class EventLog:
    """Append-only, flushed event log of one plan."""

    def __init__(self, path: str | Path, *, max_bytes: int = MAX_BYTES) -> None:
        self.path = Path(path)
        self.max_bytes = max_bytes

    @property
    def rotated(self) -> Path:
        return self.path.with_name(self.path.name + ".1")

    def last_seq(self) -> int:
        """Read from the file each time, so a re-adopting runner (or another writer) continues the count."""
        found = _last_seq(self.path)
        if found is None:
            found = _last_seq(self.rotated)
        return found or 0

    def append(self, record: Mapping[str, Any]) -> int:
        """Write ``record`` with the next ``seq``; returns that seq."""
        seq = self.last_seq() + 1
        line = json.dumps({"seq": seq, **{k: v for k, v in record.items() if k != "seq"}},
                          ensure_ascii=False, default=str).encode("utf-8") + b"\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            size = self.path.stat().st_size
        except OSError:
            size = 0
        if size and size + len(line) > self.max_bytes:
            os.replace(self.path, self.rotated)
        with open(self.path, "ab") as fh:
            if fh.tell() and not self._ends_with_newline():
                fh.write(b"\n")  # close a torn line so this record parses on its own
            fh.write(line)
            fh.flush()
        return seq

    def _ends_with_newline(self) -> bool:
        with open(self.path, "rb") as fh:
            fh.seek(-1, os.SEEK_END)
            return fh.read(1) == b"\n"

    def read_since(self, seq: int = 0, limit: int = READ_LIMIT) -> list[dict[str, Any]]:
        """Events with ``seq > seq``, oldest first, at most ``limit`` (rotated file included)."""
        out: list[dict[str, Any]] = []
        for path in (self.rotated, self.path):
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        try:
                            item = json.loads(line)
                        except ValueError:
                            continue
                        if isinstance(item, dict) and isinstance(item.get("seq"), int) and item["seq"] > seq:
                            out.append(item)
                            if len(out) >= limit:
                                return out
            except OSError:
                continue
        return out


__all__ = ["EVENT_KINDS", "EVENTS_FILE", "EventLog", "kind_matches"]
