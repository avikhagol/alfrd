"""Read-only orchestration steps, rebuilt from a session plan's event log.

Task creation records delegation and queues execution (or an orchestrator call).
Each distinct scheduler unit binds one attempt; re-adoption binds the same one.
Only validated task results complete chat work: a successful process exit is
insufficient. Mail is deliberately not an input to this projection. Waiting
message ids come from the runner's validated blocker event.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from alfrd.events import EventLog

KINDS = frozenset({"plan", "delegate", "execute", "repair", "replan", "summary"})
TERMINAL = frozenset({"done", "failed", "cancelled"})
_RANK = {"queued": 0, "running": 1, "waiting": 2, **dict.fromkeys(TERMINAL, 3)}


@dataclass(frozen=True)
class OrchestrationStep:
    id: str
    request_id: str
    kind: str
    task_row: str | None = None
    unit: str | None = None
    attempt: int = 1
    status: str = "queued"
    started_at: str | None = None
    finished_at: str | None = None
    waiting_on: list[str] = field(default_factory=list)


def step_id(plan: str, request: str, row: str, kind: str, attempt: int) -> str:
    """Stable across polling/replay; independent of list positions and UI order."""
    identity = json.dumps([plan, request, row, kind, attempt], separators=(",", ":"))
    return "step_" + hashlib.sha256(identity.encode()).hexdigest()[:32]


class StepProjection:
    """Incremental fold of authoritative events in append order. No scheduling."""

    def __init__(self) -> None:
        self._steps: dict[str, OrchestrationStep] = {}
        self._rows: dict[tuple[str, str], tuple[str, str]] = {}
        self._attempts: dict[tuple[str, str], list[str]] = {}
        self._units: dict[tuple[str, str, str], str] = {}

    @property
    def steps(self) -> list[OrchestrationStep]:
        return list(self._steps.values())

    def _new(self, plan: str, request: str, row: str, kind: str, attempt: int) -> str:
        sid = step_id(plan, request, row, kind, attempt)
        self._steps.setdefault(sid, OrchestrationStep(sid, request, kind, row, attempt=attempt))
        return sid

    def _advance(self, sid: str, status: str, at: str | None, **changes: Any) -> None:
        old = self._steps[sid]
        if old.status in TERMINAL or _RANK[status] < _RANK[old.status]:
            return
        if status == "running" and old.started_at is None:
            changes["started_at"] = at
        if status in TERMINAL:
            changes["finished_at"] = at
        self._steps[sid] = replace(old, status=status, **changes)

    def apply(self, event: Mapping[str, Any]) -> None:
        kind = event.get("kind")
        if kind not in {"task.created", "task.started", "task.finished", "task.blocked", "task.fallback",
                        "turn.started", "turn.failed", "turn.finished"}:
            return
        data = event.get("data") or {}
        plan = str(event.get("plan") or "")
        row = data.get("task") or data.get("row") or event.get("target")
        if not row:
            return
        key = (plan, row)
        if data.get("request") and data.get("task_kind"):
            task_kind = data["task_kind"]
            call_kind = "execute" if task_kind == "task" else task_kind
            if call_kind not in KINDS:
                return
            self._rows.setdefault(key, (data["request"], call_kind))
        if key not in self._rows:
            return  # unrelated workflow units / events predating task creation
        request, call_kind = self._rows[key]
        at = event.get("at")
        attempts = self._attempts.setdefault(key, [])
        if not attempts:
            attempts.append(self._new(plan, request, row, call_kind, 1))
        if kind == "task.created":
            if call_kind == "execute":
                delegated = self._new(plan, request, row, "delegate", 1)
                self._advance(delegated, "done", at, started_at=at)
            return
        unit = event.get("unit")
        sid = attempts[-1]
        if unit:
            unit_key = (plan, row, unit)
            if unit_key not in self._units:
                old = self._steps[sid]
                if old.unit is not None or old.status in TERMINAL:
                    if old.status == "waiting":
                        # The scheduler resumed in a fresh process. Retire the
                        # blocked attempt so it cannot remain the current wait.
                        self._advance(sid, "cancelled", at)
                    elif old.status == "running":
                        self._advance(sid, "failed", at)
                    # Attempt ordinals are per row: owner fallback and manual retry
                    # may reset the scheduler's own attempt_number to one.
                    sid = self._new(plan, request, row, call_kind, len(attempts) + 1)
                    attempts.append(sid)
                self._units[unit_key] = sid
                self._steps[sid] = replace(self._steps[sid], unit=unit)
            sid = self._units[unit_key]
        if kind in {"task.started", "turn.started"}:
            self._advance(sid, "running", at)
        elif kind == "task.blocked":
            self._advance(sid, "waiting", at, waiting_on=list(dict.fromkeys(data.get("waiting_on") or [])))
        elif kind == "task.finished":
            status = data.get("status")
            status = "failed" if status in {"retrying", "interrupted"} else status
            if status in TERMINAL:
                self._advance(sid, status, at)
        elif kind == "task.fallback":
            self._advance(sid, "failed", at)
        elif kind == "turn.failed" or (kind == "turn.finished" and data.get("status") != "done"):
            status = "cancelled" if data.get("status") == "cancelled" else "failed"
            self._advance(sid, status, at)


def project_steps(events: Iterable[Mapping[str, Any]]) -> list[OrchestrationStep]:
    projection = StepProjection()
    for event in events:
        projection.apply(event)
    return projection.steps


def replay_steps(path: str | Path) -> list[OrchestrationStep]:
    """Replay all retained events, including rotation, rather than the UI's 500 cap."""
    log = EventLog(path)
    projection = StepProjection()
    cursor = 0
    while batch := log.read_since(cursor):
        for event in batch:
            projection.apply(event)
        cursor = batch[-1]["seq"]
    return projection.steps


def steps_state(path: str | Path) -> list[dict[str, Any]]:
    return [asdict(step) for step in replay_steps(path)]
