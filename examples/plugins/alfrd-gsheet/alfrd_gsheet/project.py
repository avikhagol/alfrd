"""Shared project operations for the CLI and Studio; no Typer or presentation code.

Only the mapping and sync history are written. Project workflow files are read-only.
Transport calls retain the engine's request deadlines and settings dry-run policy.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from . import a1, mapping
from . import settings as plugin_settings
from .client import SyncError

if TYPE_CHECKING:
    from alfrd.execution import ExecutionConfig
    from alfrd.runtime.scheduler import Runner

    from .sync import SyncResult

MAPPING = "alfrd.gsheet.yaml"
HISTORY = Path(".alfrd") / "gsheet" / "sync.jsonl"
CLI_TIMEOUT = 60


def mapping_sha(root: Path) -> str:
    """Digest exact file bytes, including comments; empty means no mapping."""
    try:
        return hashlib.sha256((root / MAPPING).read_bytes()).hexdigest()
    except FileNotFoundError:
        return ""


def write_mapping(root: Path, text: str | None, *, base_sha256: str | None = None,
                  force: bool = False, timeout: float = CLI_TIMEOUT) -> str:
    """Check the revision and atomically replace (or delete) under the mapping lock."""
    from alfrd.runtime.plan_csv import atomic_write

    from .sync import locked

    target = root / MAPPING
    with locked(root / ".alfrd" / "locks" / f"{MAPPING}.lock", time.monotonic() + timeout):
        current = mapping_sha(root)
        if base_sha256 is not None and current != base_sha256:
            raise mapping.MappingError("The mapping changed on disk; reload.")
        if base_sha256 is None and current and not force:
            raise mapping.MappingError(f"{target} already exists; pass --force to replace it.")
        if text is None:
            target.unlink(missing_ok=True)
        else:
            atomic_write(target, text)
        return mapping_sha(root)


@dataclass(frozen=True)
class InitOutcome:
    """Starter mapping plus the values used by CLI hints and summaries."""

    path: Path
    worksheet: str
    matched: tuple[str, ...]
    steps: tuple[str, ...]
    key: str
    key_in_header: bool
    mapping: dict
    mapping_sha256: str = ""


@dataclass(frozen=True)
class PushOutcome:
    """Distinguish no matching units from a disabled or unmapped sync."""

    contexts: int
    result: SyncResult | None


def _execution(root: Path) -> ExecutionConfig:
    from alfrd.execution import load_execution

    return load_execution(root)


def plan_rows(root: Path):
    """Read the configured project plan using its execution column contract."""
    from alfrd.runtime import plan_csv

    cfg = _execution(root)
    return plan_csv.read(cfg.plan_csv, cfg.step_ids, key_column=cfg.key_column, code_column=cfg.code_column,
                         workdir_column=cfg.workdir_column, files_column=cfg.files_column).rows


def _saved() -> dict:
    from alfrd.extensions import settings

    return settings.values("gsheet")


def init_project(project: Path, *, spreadsheet: str | None = None, worksheet: str | None = None,
                 header_row: int = 1, force: bool = False, values: dict | None = None,
                 timeout: float = CLI_TIMEOUT, key_column: str | None = None,
                 code_column: str | None = None, match_against: str = "target") -> InitOutcome:
    """Create the CLI's starter mapping and return its summary without printing."""
    root = project.resolve()
    if type(header_row) is not int or header_row < 1:
        raise mapping.MappingError("header_row must be a positive integer")
    target = root / MAPPING
    if target.exists() and not force:
        raise mapping.MappingError(f"{target} already exists; pass --force to replace it.")
    cfg = _execution(root)
    saved = _saved() if values is None else values
    sid = spreadsheet or saved.get("default_spreadsheet_id")
    if not sid:
        raise mapping.MappingError("pass --spreadsheet ID or set a default spreadsheet in Settings → Plugins → Google Sheet.")
    sid = mapping.spreadsheet_id(sid)
    client = plugin_settings.make_engine(timeout, values=saved).client
    deadline = time.monotonic() + timeout
    if worksheet is None:
        sheets = client.metadata(sid, deadline=deadline).get("sheets") or []
        if not sheets:
            raise mapping.MappingError("the spreadsheet has no worksheets.")
        worksheet = str(sheets[0]["properties"]["title"])
    rows = client.get(sid, f"{a1.quote(worksheet)}!{header_row}:{header_row}", deadline=deadline)
    header = [mapping.text(v).strip() for v in (rows[0] if rows else [])]
    if not any(header):
        raise mapping.MappingError(f"row {header_row} of {worksheet!r} is empty; put the column names there or pass --header-row.")
    by_name = {h.casefold(): h for h in header if h}
    key = key_column if key_column is not None else by_name.get(cfg.key_column.casefold(), cfg.key_column)
    if key_column is not None:
        a1.column(header, key)
    data = {"version": 1, "enabled": True}
    if spreadsheet:
        data["spreadsheet_id"] = sid
    data.update(worksheet=worksheet, header_row=header_row, rows={"key_column": key})
    if match_against != "target":
        data["rows"]["match_against"] = match_against
    if code_column:
        a1.column(header, code_column)
        data["rows"]["code_column"] = code_column
    elif code_column is None and cfg.code_column and cfg.code_column.casefold() in by_name:
        data["rows"]["code_column"] = by_name[cfg.code_column.casefold()]
    matched = [s for s in cfg.step_ids if s.casefold() in by_name]
    data["outbound"] = [{"step": s, "column": by_name[s.casefold()], "field": "status"} for s in matched]
    if key_column is not None:
        mapping.validate(mapping.parse(data, saved), cfg.step_ids, header)
    if match_against == "files":
        plugin_settings.make_engine(timeout, values=saved).snapshot(mapping.parse(data, saved), cfg.step_ids,
                                                                  project_root=root)
    text = ("# Google Sheet sync for this project. Reference: the alfrd-gsheet plugin README.\n"
            + yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
            + "# More outbound examples (uncomment, then run `alfrd gsheet validate`):\n"
            "#  - {step: \"*\", column: \"{step} RAM\", field: usage.peak_mem, format: bytes}\n"
            "#  - {step: \"*\", column: \"{step} time\", field: duration_s, format: duration}\n"
            "#  - {step: \"*\", column: \"{step} log\", field: log_path_rel}\n"
            "#  - {step: \"*\", column: \"{step} finished\", field: finished, format: datetime, when: [done]}\n"
            "# Inbound example (sheet → plan CSV before a step starts):\n"
            "# inbound:\n#  - {column: Notes, to: plan_column, plan_column: notes}\n")
    sha = write_mapping(root, text, force=force, timeout=timeout)
    return InitOutcome(target, worksheet, tuple(matched), tuple(cfg.step_ids), key, key.casefold() in by_name, data, sha)


def validate_summary(project: Path, *, offline: bool = False, values: dict | None = None) -> tuple[str, str]:
    """Shared CLI/Studio validation. Warn is a mapping problem; fail is a connection/runtime error."""
    from alfrd.execution import ExecutionError

    root = project.resolve()
    try:
        if not (root / MAPPING).exists():
            return "ok", f"Sync is off: no {MAPPING}. Create one with: alfrd gsheet init {project} --spreadsheet ID"
        saved = _saved() if values is None else values
        config = mapping.load(root, saved)
        if config is None:
            return "ok", f"Sync is off: {MAPPING} has enabled: false."
        steps = _execution(root).step_ids
        problems = mapping.problems(config, steps)
        if problems:
            return "warn", f"{len(problems)} problem{'s' if len(problems) != 1 else ''}: {problems[0]}"
        rules = f"{len(config.outbound)} outbound and {len(config.inbound)} inbound rules"
        if offline:
            return "ok", f"ok: {MAPPING} is valid ({rules}); the sheet was not checked."
        state = plugin_settings.make_engine(CLI_TIMEOUT, values=saved).snapshot(config, steps, validate_rules=False, project_root=root)
        problems = mapping.problems(config, steps, state.header, first_col=state.first_col)
        if problems:
            return "warn", f"{len(problems)} problem{'s' if len(problems) != 1 else ''}: {problems[0]}"
        return "ok", (f"ok: {MAPPING} is valid ({rules}); every column exists in {state.worksheet!r} "
                      f"({len(state.row_numbers)} target rows).")
    except mapping.MappingError as exc:
        return "warn", "1 problem: " + " ".join(str(exc).split())[:800]
    except (SyncError, ExecutionError) as exc:
        return "fail", " ".join(str(exc).split())[:800]
    except Exception as exc:  # noqa: BLE001 - credentials/HTTP bodies may appear in exception text
        return "fail", f"failed ({type(exc).__name__})"


def _plans(root: Path, plan: str | None) -> list[str]:
    from alfrd.runtime import scheduler

    ids = [p["id"] for p in scheduler.list_plans(root)]
    if plan is not None:
        if plan not in ids:
            raise mapping.MappingError(f"no plan {plan!r} in {root}.")
        return [plan]
    return ids


def _runner(root: Path, plan_id: str, cache: dict) -> Runner:
    from alfrd.runtime import scheduler

    if plan_id not in cache:
        cache[plan_id] = scheduler.Runner(root, plan_id)
    return cache[plan_id]


def _split(values: list[str] | None) -> list[str]:
    return [v.strip() for item in values or [] for v in item.split(",") if v.strip()]


def push_project(project: Path, *, targets: list[str] | None = None, steps: list[str] | None = None,
                 plan: str | None = None, dry_run: bool = False, values: dict | None = None,
                 timeout: float = CLI_TIMEOUT) -> PushOutcome:
    """Backfill newest finished target/step results through the existing sync engine."""
    from alfrd.extensions import StepContext

    root = project.resolve()
    wanted_targets, wanted_steps = set(_split(targets)), _split(steps)
    known = _execution(root).step_ids
    unknown = [s for s in wanted_steps if s not in known]
    if unknown:
        raise mapping.MappingError(f"unknown step {unknown[0]!r}; steps: {', '.join(known)}.")
    newest: dict[tuple[str, str], tuple[str, str, dict]] = {}
    for plan_id in _plans(root, plan):
        for unit in _runner(root, plan_id, {}).folder.units():
            if unit.get("status") in (None, "running") or not unit.get("finished"):
                continue
            for key in unit.get("rows") or [unit.get("row")]:
                for step in unit.get("steps") or []:
                    if (wanted_steps and step not in wanted_steps) or key is None:
                        continue
                    stamp = str(unit["finished"])
                    if (key, step) not in newest or stamp > newest[key, step][0]:
                        newest[key, step] = (stamp, plan_id, unit)
    runners: dict = {}
    ctxs = []
    for (key, step), (stamp, plan_id, unit) in sorted(newest.items(), key=lambda item: item[1][0]):
        ctx = _runner(root, plan_id, runners).step_context(unit, after=True)
        rows = tuple(r for r in ctx.rows if r["key"] == str(key)
                     and (not wanted_targets or r["target"] in wanted_targets or r["key"] in wanted_targets))
        if rows:
            ctxs.append(replace(ctx, steps=(step,), rows=rows, readopted=False))
    if not ctxs:
        return PushOutcome(0, None)
    record = StepContext(str(root), plan or "", "push", "push", tuple(dict.fromkeys(s for c in ctxs for s in c.steps)),
                         ())
    engine = plugin_settings.make_engine(timeout, values=values)
    result = engine.push(ctxs, dry_run=dry_run, record=record)
    return PushOutcome(len(ctxs), result)
