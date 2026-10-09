"""Snapshot, inbound, outcome, diff and conflict handling around launched units.

The client is injected, with no Google imports here. A snapshot freezes the
mapping until that unit ends; edits apply at the next before(). Every value is
a string and each write range addresses exactly one changed cell. Re-adoption
takes a fresh snapshot without replaying inbound. Errors are recorded safely
then raised as SyncError so the core logs one plugin.hook_failed event.

Normal units use one read, at most one conflict read and at most one write.
Appending missing rows requires one fresh read under the spreadsheet lock to
allocate vacant rows without overwriting another unit's appended targets.
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import logging
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from alfrd.extensions import StepContext
from alfrd.runtime import plan_csv

from . import a1, mapping
from .client import SheetsClient, SyncError

try:
    import fcntl
except ImportError:  # Windows still serializes threads in this process
    fcntl = None

logger = logging.getLogger(__name__)
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


class ConflictError(SyncError):
    """A fail-soft conflict whose history entry was already recorded."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise SyncError("Google Sheet sync time budget exhausted")
    return remaining


@contextlib.contextmanager
def locked(path: Path, deadline: float) -> Iterator[None]:
    """Bounded thread + process locking; don't leave timed-out hook threads queued."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with _locks_guard:
        lock = _locks.setdefault(str(path.resolve()), threading.Lock())
    if not lock.acquire(timeout=_remaining(deadline)):
        raise SyncError("Google Sheet sync lock timed out")
    try:
        with path.open("a+", encoding="utf-8") as handle:
            acquired = False
            try:
                if fcntl is not None:
                    while not acquired:
                        _remaining(deadline)
                        try:
                            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                            acquired = True
                        except BlockingIOError:
                            time.sleep(min(0.05, _remaining(deadline)))
                yield
            finally:
                if acquired:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        lock.release()


@dataclass(frozen=True)
class PlanAccess:
    path: Path
    lock: Path
    steps: tuple[str, ...]
    columns: dict[str, str]

    @classmethod
    def from_context(cls, ctx: StepContext) -> PlanAccess:
        from alfrd.execution import load_execution

        root = Path(ctx.project_root)
        cfg = load_execution(root)
        try:
            plan = json.loads((root / ".alfrd" / "plans" / ctx.plan_id / "plan.json").read_text(encoding="utf-8"))
            path = Path(plan["csv"])
            steps = tuple(plan.get("steps") or cfg.step_ids)
        except (OSError, ValueError, KeyError, TypeError):
            raise SyncError("cannot load the plan CSV for Google Sheet sync") from None
        path = path if path.is_absolute() else root / path
        return cls(path, root / ".alfrd" / "locks" / f"{path.name}.lock", steps,
                   {"key_column": cfg.key_column, "code_column": cfg.code_column,
                    "workdir_column": cfg.workdir_column, "files_column": cfg.files_column})


@dataclass
class Snapshot:
    config: mapping.MappingConfig
    steps: tuple[str, ...]
    worksheet: str
    header: list[str]
    first_col: int
    values: dict[tuple[str, int], str]
    addresses: dict[tuple[str, int], str]
    row_numbers: dict[str, int]
    next_row: int
    read_at: str


@dataclass
class SyncResult:
    result: str
    changes: dict[str, str] = field(default_factory=dict)
    cells_written: int = 0
    conflicts: list[str] = field(default_factory=list)
    previous: dict[str, str] = field(default_factory=dict)  # dry run: the snapshot value of each changed cell
    labels: dict[str, dict[str, str]] = field(default_factory=dict)  # Studio preview, never recorded in history


class Sync:
    """Offline-testable hook engine. The transport must honour the supplied deadlines.

    ``timeout`` is the whole engine budget per call (default 25s), leaving margin
    below the public hook's 30s abandonment limit. Settings are captured by the
    factory; the project mapping is reloaded at each before().
    """

    def __init__(self, client: SheetsClient, settings: Mapping[str, Any] | None = None, *, timeout: float = 25,
                 plan_loader: Callable[[StepContext], PlanAccess] = PlanAccess.from_context) -> None:
        self.client = client
        self.settings = dict(settings or {})
        self.timeout = timeout
        self.plan_loader = plan_loader

    def _steps(self, ctx: StepContext) -> tuple[str, ...]:
        return self.plan_loader(ctx).steps

    def _snapshot(self, ctx: StepContext | None, config: mapping.MappingConfig, steps: Sequence[str], deadline: float,
                  *, whole_sheet: bool = False, validate_rules: bool = True) -> Snapshot:
        title = (self.client.worksheet_title(config.spreadsheet_id, config.worksheet, deadline=deadline)
                 if isinstance(config.worksheet, int) else config.worksheet)
        range_ = a1.quote(title) + ("!" + config.read_range if config.read_range and not whole_sheet else "")
        _remaining(deadline)
        rows = self.client.get(config.spreadsheet_id, range_, deadline=deadline)
        _remaining(deadline)
        first_row, first_col = a1.origin(config.read_range if not whole_sheet else None)
        header_index = config.header_row - first_row
        if not 0 <= header_index < len(rows):
            raise mapping.MappingError("sheet header_row is empty or outside read_range")
        header = [mapping.text(v) for v in rows[header_index]]
        names = [config.key_column, config.code_column, *(r.column for r in config.inbound),
                 *(r.column.replace("{step}", step) for r in config.outbound
                   for step in (steps if r.step == "*" else [r.step]))]
        # Google trims trailing empty cells. Literal letters can address columns
        # with no header; retain their addresses even when absent from values.get.
        width = max([len(header), *(len(r) for r in rows)])
        for name in names:
            if (name and name.strip().casefold() not in {h.strip().casefold() for h in header}
                    and name.isascii() and name.isalpha() and name.isupper()):
                col = a1.index(name)
                if col > 18278:
                    raise mapping.MappingError("unknown sheet column or column beyond ZZZ")
                width = max(width, col - first_col + 1)
        if config.read_range and not whole_sheet:
            end = config.read_range.split(":")[-1]
            end_col = a1.index("".join(c for c in end if c.isalpha()))
            if first_col + width - 1 > end_col:
                raise mapping.MappingError("mapped sheet column is outside read_range")
        header.extend([""] * (width - len(header)))
        checked = config if validate_rules else replace(config, outbound=(), inbound=())
        mapping.validate(checked, steps, header, first_col=first_col)
        key_col = a1.column(header, config.key_column, first_col=first_col)
        code_col = a1.column(header, config.code_column, first_col=first_col) if config.code_column else None
        values, addresses, row_numbers = {}, {}, {}
        for offset, raw in enumerate(rows[header_index + 1:], start=config.header_row + 1):
            def at(col: int, raw: list = raw) -> str:
                pos = col - first_col
                return mapping.text(raw[pos]) if pos < len(raw) else ""

            target = at(key_col).strip()
            if not target:
                continue
            key = plan_csv.row_key(target, at(code_col).strip() if code_col else "")
            if key in row_numbers:
                raise mapping.MappingError("duplicate sheet row identity; use rows.code_column if necessary")
            row_numbers[key] = offset
            for col in range(first_col, first_col + len(header)):
                values[key, col] = at(col)
                addresses[key, col] = a1.cell(title, offset, col)
        return Snapshot(config, tuple(steps), title, header, first_col, values, addresses, row_numbers,
                        max(config.header_row + 1, first_row + len(rows)), _now())

    @staticmethod
    def _key(row: Mapping[str, str], state: Snapshot) -> str:
        return plan_csv.row_key(row["target"], row.get("code", "") if state.config.code_column else "")

    def _inbound(self, ctx: StepContext, state: Snapshot, deadline: float) -> None:
        if not state.config.inbound or self.settings.get("dry_run", False):
            return
        access = self.plan_loader(ctx)
        table = plan_csv.read(access.path, access.steps, **access.columns)
        cells, only_if = {}, {}
        for rule in state.config.inbound:
            dest = rule.destination
            if rule.to == "plan_column":
                matches = [h for h in table.header if h.strip().casefold() == dest.casefold()]
                if len(matches) != 1:
                    raise mapping.MappingError("inbound plan_column must name an existing, unambiguous extra column")
                dest = matches[0]
                reserved = {s.casefold() for s in access.steps} | {c.casefold() for c in access.columns.values()}
                if dest.casefold() in reserved:
                    raise mapping.MappingError("inbound plan_column must be an extra column, not a step or row identity")
            elif dest in ctx.steps:
                continue  # never alter any step in the unit that is about to spawn
            col = a1.column(state.header, rule.column, first_col=state.first_col)
            for row in ctx.rows:
                key = self._key(row, state)
                if key not in state.row_numbers:
                    continue
                val = state.values.get((key, col), "")
                if rule.to == "plan_cell":
                    val = val.strip().lower()
                    if val not in {"todo", "skip"}:
                        continue
                    only_if[row["key"], dest] = set(plan_csv.STATES) - {"running"}
                cells[row["key"], dest] = val
        if cells:
            _remaining(deadline)
            # Reuse the runner's exact CSV lock; re-read and only_if are inside it.
            plan_csv.update(access.path, access.lock, access.steps, cells, only_if=only_if,
                            lock_timeout=_remaining(deadline), **access.columns)

    def _load(self, ctx: StepContext, steps: Sequence[str], deadline: float, *,
              plan_steps: Sequence[str] | None = None) -> Snapshot | None:
        """None (and no API call) when the mapping is absent, disabled or has no rule for ``steps``."""
        config = mapping.load(ctx.project_root, self.settings)
        if config is None or not config.applies(steps):
            return None
        plan_steps = tuple(plan_steps) if plan_steps is not None else self._steps(ctx)
        mapping.validate(config, plan_steps)
        return self._snapshot(ctx, config, plan_steps, deadline)

    def snapshot(self, config: mapping.MappingConfig, steps: Sequence[str], *, validate_rules: bool = True) -> Snapshot:
        """One read that checks the mapping against the live header (CLI/Studio validate)."""
        checked = config if validate_rules else replace(config, outbound=(), inbound=())
        mapping.validate(checked, steps)
        return self._snapshot(None, config, steps, time.monotonic() + self.timeout, validate_rules=validate_rules)

    def before(self, ctx: StepContext) -> Snapshot | None:
        deadline = time.monotonic() + self.timeout
        try:
            state = self._load(ctx, ctx.steps, deadline)
            if state is not None:
                self._inbound(ctx, state, deadline)
            return state
        except Exception as exc:  # noqa: BLE001 - record safely, then core reports hook failure
            self._failed(ctx, exc, "before")

    def _diff(self, ctxs: Sequence[StepContext], state: Snapshot, *, append: bool = False,
              labels: dict | None = None) -> dict[str, str]:
        """Changed cells for all ``ctxs`` in order (a later context wins a shared cell)."""
        working = state.values.copy()
        for ctx in ctxs:
            self._diff_one(ctx, state, working, append, labels)
        return {state.addresses[key]: val for key, val in working.items() if val != state.values.get(key, "")}

    def _diff_one(self, ctx: StepContext, state: Snapshot, working: dict, append: bool,
                  labels: dict | None = None) -> None:
        seen: set[str] = set()
        for row in ctx.rows:
            key = self._key(row, state)
            if key in seen:
                raise mapping.MappingError("unit rows share a sheet identity; configure rows.code_column")
            seen.add(key)
            matching = [(step, rule) for step in ctx.steps for rule in state.config.outbound
                        if (rule.step == "*" or rule.step == step)
                        and (not rule.when or mapping.resolve(ctx, row, step, "status") in rule.when)]
            if not matching:
                continue
            if key not in state.row_numbers:
                if not append:
                    continue
                row_number = state.next_row
                state.next_row += 1
                state.row_numbers[key] = row_number
                for col in range(state.first_col, state.first_col + len(state.header)):
                    state.addresses[key, col] = a1.cell(state.worksheet, row_number, col)
                key_col = a1.column(state.header, state.config.key_column, first_col=state.first_col)
                working[key, key_col] = row["target"]
                if labels is not None:
                    labels[state.addresses[key, key_col]] = {"target": row["target"], "step": "",
                                                            "column": state.config.key_column}
                if state.config.code_column:
                    code_col = a1.column(state.header, state.config.code_column, first_col=state.first_col)
                    working[key, code_col] = row.get("code", "")
                    if labels is not None:
                        labels[state.addresses[key, code_col]] = {"target": row["target"], "step": "",
                                                                 "column": state.config.code_column}
            for step, rule in matching:
                col = a1.column(state.header, rule.column.replace("{step}", step), first_col=state.first_col)
                working[key, col] = mapping.value(ctx, row, step, rule)
                if labels is not None:
                    labels[state.addresses[key, col]] = {"target": row["target"], "step": step,
                                                        "column": rule.column.replace("{step}", step)}

    def after(self, ctx: StepContext, state: Snapshot | None) -> SyncResult | None:
        deadline = time.monotonic() + self.timeout
        try:
            if state is None:
                if not ctx.readopted:
                    return None
                state = self._load(ctx, ctx.steps, deadline)
                if state is None:
                    return None
            result = self._write([ctx], state, deadline, record=ctx)
            self._record(ctx, state, result)
            return result
        except Exception as exc:  # noqa: BLE001 - record safely, then core reports hook failure
            self._failed(ctx, exc, "after")

    def push(self, ctxs: Sequence[StepContext], *, dry_run: bool = False,
             record: StepContext | None = None) -> SyncResult | None:
        """One snapshot and at most one write for finished units (CLI ``diff``/``push``).

        None (no API call) when the mapping is off or has no rule for these steps.
        ``dry_run`` (or the dry_run setting) previews without writing. Otherwise the
        outcome is recorded in sync.jsonl as ``record`` with phase ``push``. Errors are
        raised as-is (MappingError/SyncError text is safe) and are not recorded.
        """
        if not ctxs:
            return None
        deadline = time.monotonic() + self.timeout
        steps = list(dict.fromkeys(s for ctx in ctxs for s in ctx.steps))
        # Units may come from plans with different step lists: "*" rules expand over all of them.
        first = {ctx.plan_id: ctx for ctx in reversed(ctxs)}.values()
        plan_steps = list(dict.fromkeys(s for ctx in first for s in self._steps(ctx)))
        state = self._load(ctxs[0], steps, deadline, plan_steps=plan_steps)
        if state is None:
            return None
        result = self._write(ctxs, state, deadline, dry_run=dry_run or None, record=record, phase="push")
        if result.result != "dry run" and record is not None:
            self._record(record, state, result, phase="push")
        return result

    def _write(self, ctxs: Sequence[StepContext], state: Snapshot, deadline: float, *, dry_run: bool | None = None,
               record: StepContext | None = None, phase: str = "after") -> SyncResult:
        state = copy.deepcopy(state)  # dry-run/replay must not mutate the before snapshot
        dry_run = bool(self.settings.get("dry_run", False)) if dry_run is None else dry_run
        policy = self.settings.get("conflict_policy", "skip")
        option = self.settings.get("value_input_option", "RAW")
        if policy not in {"overwrite", "skip", "fail-soft"} or option not in {"RAW", "USER_ENTERED"}:
            raise SyncError("invalid Google Sheet conflict_policy or value_input_option setting")
        missing = (state.config.missing_row == "append" and any(self._key(r, state) not in state.row_numbers
                   and any((rule.step == "*" or rule.step in ctx.steps)
                           and (not rule.when or any(mapping.resolve(ctx, r, s, "status") in rule.when
                                                    for s in ctx.steps if rule.step in (s, "*")))
                           for rule in state.config.outbound) for ctx in ctxs for r in ctx.rows))
        if dry_run:
            labels = {}
            changes = self._diff(ctxs, state, append=missing, labels=labels)
            baseline = {address: state.values.get(key, "") for key, address in state.addresses.items()}
            return SyncResult("dry run", changes, previous={a: baseline.get(a, "") for a in changes},
                              labels={a: labels[a] for a in changes})
        changes = self._diff(ctxs, state)
        if not changes and not missing:
            logger.debug("gsheet: no changes for %s", ", ".join(c.unit_id for c in ctxs))
            return SyncResult("no changes")
        name = hashlib.sha256(state.config.spreadsheet_id.encode()).hexdigest()[:24]
        path = Path(ctxs[0].project_root) / ".alfrd" / "locks" / f"gsheet-{name}.lock"
        with locked(path, deadline):
            appended_rows = []
            if missing:
                fresh = self._snapshot(ctxs[0], state.config, state.steps, deadline, whole_sheet=True)
                if fresh.header[state.first_col - 1:state.first_col - 1 + len(state.header)] != state.header:
                    raise SyncError("Google Sheet append skipped: the header changed during the step")
                # Preserve original baselines for existing rows. Only missing row
                # identities/addresses come from the current, locked sheet read.
                for row in (r for c in ctxs for r in c.rows):
                    key = self._key(row, state)
                    if key in state.row_numbers or key not in fresh.row_numbers:
                        continue
                    state.row_numbers[key] = fresh.row_numbers[key]
                    for k, v in fresh.values.items():
                        if k[0] == key:
                            state.values[k] = v
                            state.addresses[k] = fresh.addresses[k]
                state.next_row = fresh.next_row
                appended_rows = list(dict.fromkeys(self._key(r, state) for c in ctxs for r in c.rows
                                                   if self._key(r, state) not in state.row_numbers))
                changes = self._diff(ctxs, state, append=True)
            if not changes:
                return SyncResult("no changes")
            conflicts = []
            if state.config.verify_before_write:
                ranges = list(changes)
                _remaining(deadline)
                current = self.client.batch_get(state.config.spreadsheet_id, ranges, deadline=deadline)
                _remaining(deadline)
                if len(current) != len(ranges):
                    raise SyncError("Google Sheet conflict read returned incomplete results")
                baseline = {address: state.values.get(key, "") for key, address in state.addresses.items()}
                for address, values in zip(ranges, current):
                    live = mapping.text(values[0][0]) if values and values[0] else ""
                    if live != baseline.get(address, ""):
                        conflicts.append(address)
                identity_cols = {a1.column(state.header, state.config.key_column, first_col=state.first_col)}
                if state.config.code_column:
                    identity_cols.add(a1.column(state.header, state.config.code_column, first_col=state.first_col))
                if any(state.addresses.get((key, col)) in conflicts for key in appended_rows for col in identity_cols):
                    raise SyncError("Google Sheet append skipped: a row identity changed before writing")
                if conflicts and policy == "fail-soft":
                    if record is not None:
                        self._record(record, state, SyncResult("skipped (conflict)", changes, conflicts=conflicts),
                                     phase=phase)
                    # The core logs the warning + plugin.hook_failed; don't duplicate history.
                    raise ConflictError("Google Sheet sync skipped: cells changed during the step")
                if policy == "skip":
                    changes = {address: v for address, v in changes.items() if address not in conflicts}
            if not changes:
                return SyncResult("skipped (conflict)", conflicts=conflicts)
            _remaining(deadline)
            data = [{"range": address, "values": [[val]]} for address, val in changes.items()]
            self.client.batch_update(state.config.spreadsheet_id, data, value_input_option=option, deadline=deadline)
            return SyncResult("ok", changes, len(changes), conflicts)

    def _record(self, ctx: StepContext, state: Snapshot | None, result: SyncResult, *, error: str = "", phase: str = "after") -> None:
        path = Path(ctx.project_root) / ".alfrd" / "gsheet" / "sync.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"at": _now(), "plan_id": ctx.plan_id, "unit_id": ctx.unit_id, "steps": list(ctx.steps),
                  "phase": phase, "result": result.result, "cells_written": result.cells_written,
                  "conflicts": result.conflicts, "changed_cells": list(result.changes), "error": error}
        if state is not None:
            record.update(spreadsheet_id=state.config.spreadsheet_id, worksheet=state.worksheet, read_at=state.read_at)
        # Deliberately omit values, context and settings: result/error fields can
        # contain credentials produced by a command or an unsafe HTTP transport.
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _failed(self, ctx: StepContext, exc: Exception, phase: str) -> None:
        message = (str(exc) if isinstance(exc, (SyncError, mapping.MappingError))
                   else f"Google Sheet sync failed ({type(exc).__name__})")
        for key, val in self.settings.items():
            if key == "credentials_json" and val:
                message = message.replace(str(val), "<credentials>")
                with contextlib.suppress(ValueError, TypeError):
                    credentials = json.loads(str(val))
                    if isinstance(credentials, dict):
                        for secret in credentials.values():
                            if isinstance(secret, str) and secret:
                                message = message.replace(secret, "<credentials>")
        message = " ".join(message.split())[:800]
        # fail-soft already recorded the conflict result in _write.
        if not isinstance(exc, ConflictError):
            with contextlib.suppress(OSError):
                self._record(ctx, None, SyncResult("error"), error=message, phase=phase)
        raise SyncError(message) from None
