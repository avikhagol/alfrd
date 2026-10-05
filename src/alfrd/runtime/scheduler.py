"""Run a plan CSV (targets × steps) with the commands from alfrd.yaml.

State lives next to the project, so it outlives ``alfrd serve`` and does not
depend on the runtime database::

    <root>/.alfrd/plans/<plan_id>/
        plan.json      settings, status, runner pid/host/heartbeat (runner writes)
        control.json   {"action": "run" | "pause" | "cancel"}          (CLI/Studio write)
        runner.lock    flock held by the live runner
        runner.log     the runner's own output
        units/<id>.json  one per command: argv, pid, times, exit code, verified steps
        logs/<id>.log    command output;  logs/<id>.exit / .child (from the shim)

A runner is a detached process (new session). Each command runs through
``alfrd.runtime.shim`` in its own session with output going straight to its log
file, so a command keeps running when the runner, the server or the terminal
goes away. A new runner re-adopts commands that are still alive, finalizes the
ones that ended (exit file / result CSV) and continues the queue.
"""

from __future__ import annotations

import contextlib
import csv
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import statistics
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from alfrd.execution import ExecutionConfig, ExecutionError, RenderError, load_execution, render, split_files

from . import plan_csv as pc
from .shim import proc_start

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

PLANS_DIR = Path(".alfrd") / "plans"
ACTIVE = ("running", "paused")
POLL = 1.0


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, path)


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9+._-]+", "_", text).strip("_")[:60] or "x"


def _parse_stamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).strip())
    except ValueError:
        return None
    if stamp.tzinfo is not None:
        stamp = stamp.astimezone().replace(tzinfo=None)
    return stamp


def pid_alive(pid: int | None, start: str | None = None, host: str | None = None) -> bool:
    """Is ``pid`` (on this host, started at ``start``) still running and not a zombie?"""
    if not pid or (host and host != socket.gethostname()):
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    stat = Path(f"/proc/{pid}/stat")
    if stat.exists():
        try:
            text = stat.read_text()
            if text[text.rfind(")") + 2:].split()[0] in ("Z", "X"):
                return False
        except OSError:
            return False
        if start and proc_start(int(pid)) != start:
            return False
    return True


# ---------------------------------------------------------------------------
# Plan folders


@dataclass
class PlanDir:
    root: Path
    id: str

    @property
    def path(self) -> Path:
        return self.root / PLANS_DIR / self.id

    @property
    def plan_file(self) -> Path:
        return self.path / "plan.json"

    @property
    def control_file(self) -> Path:
        return self.path / "control.json"

    @property
    def lock_file(self) -> Path:
        return self.path / "runner.lock"

    @property
    def runner_log(self) -> Path:
        return self.path / "runner.log"

    @property
    def units_dir(self) -> Path:
        return self.path / "units"

    @property
    def logs_dir(self) -> Path:
        return self.path / "logs"

    def load(self) -> dict[str, Any]:
        data = _read_json(self.plan_file)
        if not isinstance(data, dict):
            raise ExecutionError(f"plan {self.id!r} not found in {self.root}")
        return data

    def save(self, data: Mapping[str, Any]) -> None:
        _write_json(self.plan_file, dict(data))

    def control(self) -> str:
        data = _read_json(self.control_file, {}) or {}
        return str(data.get("action") or "run")

    def set_control(self, action: str, **extra: Any) -> None:
        if action not in ("run", "pause", "cancel"):
            raise ExecutionError(f"unknown control action {action!r}")
        _write_json(self.control_file, {"action": action, "at": now_iso(), **extra})

    @property
    def overrides_file(self) -> Path:
        return self.path / "overrides.json"

    def overrides(self) -> dict[str, dict[str, Any]]:
        """Per-step settings changed on this plan while it runs (``{step id: {setting: value}}``)."""
        data = _read_json(self.overrides_file, {}) or {}
        return {str(k): dict(v) for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}

    def units(self) -> list[dict[str, Any]]:
        if not self.units_dir.is_dir():
            return []
        out = [_read_json(p) for p in sorted(self.units_dir.glob("*.json"))]
        return [u for u in out if isinstance(u, dict)]

    def save_unit(self, unit: Mapping[str, Any]) -> None:
        _write_json(self.units_dir / f"{unit['id']}.json", dict(unit))

    def csv_path(self, plan: Mapping[str, Any] | None = None) -> Path:
        plan = plan or self.load()
        path = Path(plan["csv"])
        return path if path.is_absolute() else self.root / path

    def csv_lock(self, plan: Mapping[str, Any] | None = None) -> Path:
        return self.root / ".alfrd" / "locks" / f"{self.csv_path(plan).name}.lock"

    def runner_alive(self) -> bool:
        """True while a runner holds ``runner.lock`` (flock), same host only."""
        if fcntl is None or not self.lock_file.exists():
            info = (_read_json(self.plan_file, {}) or {}).get("runner") or {}
            return pid_alive(info.get("pid"), info.get("proc_start"), info.get("host")) and not info.get("stopped")
        with open(self.lock_file, "a+") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return True
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            return False


def plans_root(root: str | Path) -> Path:
    return Path(root).resolve() / PLANS_DIR


def list_plans(root: str | Path) -> list[dict[str, Any]]:
    base = plans_root(root)
    if not base.is_dir():
        return []
    out = []
    for folder in base.iterdir():
        data = _read_json(folder / "plan.json")
        if isinstance(data, dict):
            out.append(data)
    return sorted(out, key=lambda p: str(p.get("created") or ""), reverse=True)


def active_plan(root: str | Path, csv_file: str | Path | None = None, target: str | None = None) -> dict[str, Any] | None:
    for plan in list_plans(root):
        if plan.get("status") in ACTIVE + ("interrupted",):
            if csv_file is None or Path(plan["csv"]).name == Path(csv_file).name:
                if target is None or not plan.get("targets") or target in plan["targets"]:
                    return plan
    return None


def table_for(cfg: ExecutionConfig, path: Path, steps: Sequence[str] | None = None) -> pc.PlanTable:
    return pc.read(path, steps or cfg.step_ids, key_column=cfg.key_column, code_column=cfg.code_column,
                   workdir_column=cfg.workdir_column, files_column=cfg.files_column)


def _columns(cfg: ExecutionConfig) -> dict[str, str]:
    return {"key_column": cfg.key_column, "code_column": cfg.code_column,
            "workdir_column": cfg.workdir_column, "files_column": cfg.files_column}


# ---------------------------------------------------------------------------
# Units: what runs next, and the command for it


@dataclass
class UnitSpec:
    rows: list[pc.PlanRow]
    steps: list[str]
    mode: str

    @property
    def row_key(self) -> str:
        return self.rows[0].key if self.mode != "batch" else "*"

    @property
    def target(self) -> str:
        return self.rows[0].target if self.mode != "batch" else ""


def next_step(row: pc.PlanRow, steps: Sequence[str], on_failure: str) -> str | None:
    """First ``todo`` cell of a row, in step order; None when the row is done or stopped."""
    for step in steps:
        value = row.cell(step)
        if value == pc.TODO:
            return step
        if value == pc.RUNNING:
            return None
        if value in pc.STOP_VALUES and on_failure != "continue":
            return None
    return None


def unit_for_row(row: pc.PlanRow, steps: Sequence[str], mode: str, on_failure: str) -> UnitSpec | None:
    first = next_step(row, steps, on_failure)
    if first is None:
        return None
    if mode == "step":
        return UnitSpec([row], [first], mode)
    tail = list(steps)[list(steps).index(first):]
    return UnitSpec([row], [s for s in tail if row.cell(s) == pc.TODO], mode)


# ---------------------------------------------------------------------------
# Concurrent rows: serialize the ones that conflict (execution.serialize_on)


def _shared(a: Mapping[str, set[str]], b: Mapping[str, set[str]], names: Sequence[str], match: str) -> list[tuple[str, list[str]]]:
    """What rows ``a`` and ``b`` share, per name; [] when they don't conflict."""
    shared: list[tuple[str, list[str]]] = []
    for name in names:
        common = a.get(name, set()) & b.get(name, set())
        if common:
            shared.append((name, sorted(common)))
        elif match == "all":
            return []
    return shared


def _row_label(row: pc.PlanRow) -> str:
    return f"{row.target} ({row.code})" if row.code else row.target


def _describe(shared: Sequence[tuple[str, list[str]]], match: str) -> str:
    parts = []
    for name, values in shared:
        if name == "target":
            parts.append("same target")
        elif name == "files":
            shown = ", ".join(Path(v).name for v in values[:2]) + (" …" if len(values) > 2 else "")
            parts.append(f"shares {shown}")
        else:
            parts.append(f"same {name} {values[0]}")
    return (" and " if match == "all" else ", ").join(parts)


def serialize_settings(plan: Mapping[str, Any], cfg: ExecutionConfig | None = None) -> tuple[list[str], str]:
    """``(serialize_on, serialize_match)`` of a plan (plans made before 0.2.0.8: from alfrd.yaml)."""
    if "serialize_on" in plan:
        return list(plan.get("serialize_on") or []), str(plan.get("serialize_match") or "all")
    settings = cfg.settings if cfg is not None else {}
    return list(settings.get("serialize_on") or []), str(settings.get("serialize_match") or "all")


@dataclass
class Picked:
    start: list[tuple[pc.PlanRow, UnitSpec]]
    waiting: list[dict[str, Any]]


def pick_rows(table: pc.PlanTable, *, mode: str, on_failure: str, limit: int, free: int,
              names: Sequence[str], match: str = "all", base: str | Path | None = None,
              running_rows: Iterable[str] = (), started_rows: Iterable[str] = (),
              locks: Iterable[str] = ()) -> Picked:
    """Rows to start now (CSV order, at most ``free``) and why others wait.

    With ``limit > 1`` a row holds its ``serialize_on`` values from its first
    step until it has nothing left to run (not per step), so no conflicting row
    slips in between two of its steps. A blocked row doesn't hold up later rows
    it doesn't conflict with; rows that conflict keep their CSV order. Only when
    no ``serialize_on`` rule is declared, rows of the same work dir
    (``locks``: ``code/workdir`` of live units) run one at a time as a safety net;
    a declared rule (AVICA: shared FITS files) is the whole story.
    With ``limit == 1`` nothing here changes what runs next.
    """
    running = set(running_rows)
    started = set(started_rows)
    names = list(names) if limit > 1 and mode != "batch" else []
    locks = set(locks)
    keys_cache: dict[str, dict[str, set[str]]] = {}

    def keys_of(row: pc.PlanRow) -> dict[str, set[str]]:
        if row.key not in keys_cache:
            keys_cache[row.key] = pc.conflict_keys(row, names, table, base)
        return keys_cache[row.key]

    order = {row.key: i for i, row in enumerate(table.rows)}
    # Holders: running rows always; rows between two of their steps (started, work left) too.
    live_holders = [row for row in table.rows if row.key in running] if names else []
    paused_holders = [row for row in table.rows if names and row.key not in running and row.key in started
                      and unit_for_row(row, table.steps, mode, on_failure)]
    chosen: list[pc.PlanRow] = []
    start: list[tuple[pc.PlanRow, UnitSpec]] = []
    waiting: list[dict[str, Any]] = []
    passed: list[pc.PlanRow] = []  # rows with work that did not start: later conflicting rows stay behind them

    def conflict(row: pc.PlanRow, others: Iterable[pc.PlanRow], note: str = "") -> tuple[pc.PlanRow, str] | None:
        mine = keys_of(row)
        for other in others:
            if other.key == row.key:
                continue
            shared = _shared(mine, keys_of(other), names, match)
            if shared:
                return other, f"waiting: {_describe(shared, match)} with {_row_label(other)}{note}"
        return None

    for row in table.rows:
        if row.key in running:
            continue
        spec = unit_for_row(row, table.steps, mode, on_failure)
        if spec is None:
            continue
        blocked = None
        if names:
            mid_row = row.key in started
            # A row between two steps only waits for running rows, rows picked this pass, and
            # conflicting rows that are also mid-row and earlier in the CSV (so one of them always goes).
            # A row that has not started also waits for every mid-row holder and for earlier
            # conflicting rows that did not start yet (CSV order).
            others = [*live_holders, *chosen, *(h for h in paused_holders if not mid_row or order[h.key] < order[row.key])]
            blocked = conflict(row, others)
            if blocked is None and not mid_row:
                blocked = conflict(row, passed, " (earlier in the plan)")
        lock = f"{row.code}/{row.workdir}" if row.code and limit > 1 and not names else None
        if blocked is None and lock and lock in locks:
            blocked = (None, f"waiting: work dir {lock} is in use")
        if blocked is not None:
            other, reason = blocked
            waiting.append({"row": row.key, "target": row.target, "code": row.code, "reason": reason,
                            "blocked_by": other.key if other is not None else lock})
            passed.append(row)
            continue
        if len(start) >= free:
            passed.append(row)
            continue
        start.append((row, spec))
        chosen.append(row)
        if lock:
            locks.add(lock)
    return Picked(start, waiting)


def conflict_groups(table: pc.PlanTable, names: Sequence[str], match: str = "all",
                    base: str | Path | None = None, rows: Sequence[pc.PlanRow] | None = None) -> list[list[str]]:
    """Rows (with work) grouped into connected components of conflicts; each group runs one row at a time."""
    rows = list(rows if rows is not None else table.rows)
    parent = {r.key: r.key for r in rows}

    def find(k: str) -> str:
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    if names:
        keys = {r.key: pc.conflict_keys(r, names, table, base) for r in rows}
        for i, a in enumerate(rows):
            for b in rows[i + 1:]:
                if _shared(keys[a.key], keys[b.key], names, match):
                    parent[find(b.key)] = find(a.key)
    groups: dict[str, list[str]] = {}
    for r in rows:
        groups.setdefault(find(r.key), []).append(r.key)
    return list(groups.values())


def values_for(cfg: ExecutionConfig, unit: UnitSpec, plan: Mapping[str, Any], csv_file: Path) -> dict[str, Any]:
    base: dict[str, Any] = {
        "root": str(cfg.root),
        "plan_csv": str(csv_file),
        "plan_id": plan.get("id", ""),
        "steps": ",".join(unit.steps),
        "from_step": unit.steps[0] if unit.steps else "",
        "step": unit.steps[0] if len(unit.steps) == 1 else "",
    }
    if unit.mode == "step" and unit.steps:
        step = cfg.step(unit.steps[0])
        archive = cfg.root / PLANS_DIR / str(plan.get("id") or "preview") / "handoffs" / str(plan.get("unit_id") or unit.steps[0])
        base.update(prompt_file=str(archive / "prompt.md"), response_file=str(archive / "response.md"),
                    iteration=step.iteration, iterations=step.iterations)
    if unit.mode == "batch":
        base["targets"] = ",".join(r.target for r in unit.rows)
        return base
    row = unit.rows[0]
    base.update({k: v for k, v in row.values.items() if k})
    base.update({
        "target": row.target,
        "project_code": row.code,
        "workdir": row.workdir,
        "targets": row.target,
        cfg.files_column: split_files(row.files),
    })
    return base


def command_for(cfg: ExecutionConfig, unit: UnitSpec, plan: Mapping[str, Any], csv_file: Path) -> tuple[list[str], dict[str, str], float | None]:
    """Rendered argv, extra env and timeout for a unit. Raises ExecutionError."""
    if unit.mode == "step":
        spec = cfg.step(unit.steps[0])
        if not spec.argv:
            raise ExecutionError(
                f"no command for step {spec.id!r}: set execution.step_entrypoint, "
                "workflows[].entrypoint or the step's entrypoint/cmd in alfrd.yaml"
            )
        argv, env, timeout = spec.argv, dict(spec.env), spec.timeout
    else:
        entry = cfg.unit_entrypoint(unit.mode)
        if entry is None:
            raise ExecutionError(f"mode {unit.mode!r} needs execution.{unit.mode}_entrypoint in alfrd.yaml")
        argv, env = entry[1], {}
        timeout = cfg.settings.get("timeout")
        timeout = float(timeout) if timeout else None
    rendered = render(argv, values_for(cfg, unit, plan, csv_file))
    return rendered, env, timeout


def preview(root: str | Path, csv_file: str | Path | None = None, *, mode: str | None = None,
            on_failure: str | None = None, cfg: ExecutionConfig | None = None,
            concurrency: int | None = None) -> dict[str, Any]:
    """Commands the runner would start, in order, if every command succeeded.

    ``conflicts`` groups the rows with work into conflict groups (rows in one
    group run one at a time) and says how many can run at once.
    """
    cfg = cfg or load_execution(root)
    path = Path(csv_file) if csv_file else cfg.plan_csv
    if not path.is_absolute():
        path = cfg.root / path
    table = table_for(cfg, path)
    mode = mode or cfg.mode
    on_failure = on_failure or cfg.settings["on_failure"]
    plan = {"id": "(preview)"}
    items: list[dict[str, Any]] = []
    errors: list[str] = []
    if not path.exists():
        errors.append(f"plan CSV {path} does not exist")
    missing_steps = [s for s in cfg.step_ids if s not in table.header]
    specs: list[UnitSpec] = []
    if mode == "batch":
        rows = [r for r in table.rows if any(r.cell(s) == pc.TODO for s in table.steps)]
        if rows:
            specs.append(UnitSpec(rows, [s for s in table.steps if any(r.cell(s) == pc.TODO for r in rows)], mode))
    else:
        for row in table.rows:
            done: set[str] = set()
            while True:
                fake = pc.PlanRow(values={**row.values, **{s: "done" for s in done}}, index=row.index, key=row.key,
                                  target=row.target, code=row.code, workdir=row.workdir, files=row.files)
                spec = unit_for_row(fake, table.steps, mode, on_failure)
                if spec is None:
                    break
                specs.append(UnitSpec([row], spec.steps, mode))
                done.update(spec.steps)
                if mode != "step":
                    break
    for spec in specs:
        item: dict[str, Any] = {"row": spec.row_key, "target": spec.target, "steps": spec.steps,
                                "code": spec.rows[0].code if spec.mode != "batch" else "", "mode": spec.mode}
        try:
            argv, _env, timeout = command_for(cfg, spec, plan, path)
            item.update(argv=argv, timeout=timeout)
        except RenderError as exc:
            item.update(error=str(exc), missing=exc.missing)
        except ExecutionError as exc:
            item.update(error=str(exc))
        items.append(item)
    limit = max(1, int(concurrency or cfg.settings.get("concurrency") or 1))
    names, match = serialize_settings({}, cfg)
    with_work = [r for r in table.rows if unit_for_row(r, table.steps, mode, on_failure)] if mode != "batch" else []
    groups = conflict_groups(table, names if limit > 1 else [], match, cfg.cwd, with_work)
    parallel = 1 if mode == "batch" else min(limit, len(groups)) if groups else 0
    conflicts = {
        "rows": len(with_work), "groups": [g for g in groups if len(g) > 1], "group_count": len(groups),
        "concurrency": limit, "parallel": parallel, "serialize_on": names if limit > 1 else [], "serialize_match": match,
        "text": (f"{len(with_work)} row(s), {len(groups)} conflict group(s), up to {parallel} at once"
                 if mode != "batch" else "batch: one command for the whole plan"),
    }
    return {
        "csv": str(path),
        "mode": mode,
        "conflicts": conflicts,
        "cwd": str(cfg.cwd),
        "steps": table.steps,
        "missing_step_columns": missing_steps,
        "unknown_columns": table.unknown_steps,
        "rows": len(table.rows),
        "units": items,
        "errors": errors + sorted({i["error"] for i in items if i.get("error")}),
    }


# ---------------------------------------------------------------------------
# Result CSV verification (AVICA writes one row per step attempt)


def _row_success(row: Mapping[str, str]) -> bool:
    try:
        return int(row.get("success_count") or 0) > 0 and int(row.get("failed_count") or 0) == 0
    except ValueError:
        return False


def verify_steps(cfg: ExecutionConfig, target: str, code: str, workdir: str, steps: Iterable[str],
                 since: datetime) -> dict[str, dict[str, Any]]:
    """Latest result-CSV row per step started at/after ``since`` (minus 60 s)."""
    from alfrd.avica_layout import result_csv_matches
    from alfrd.studio_defs import resolve_alias

    wanted = set(steps)
    cutoff = since - timedelta(seconds=60)
    found: dict[str, dict[str, Any]] = {}
    if not target:
        return found
    try:
        matches = result_csv_matches(cfg.root, target, code, workdir)
    except Exception:  # noqa: BLE001 - no AVICA layout: nothing to verify against
        return found
    for item in matches:
        try:
            with open(item["path"], newline="", encoding="utf-8-sig") as stream:
                rows = list(csv.DictReader(stream))
        except OSError:
            continue
        for row in rows:
            step = resolve_alias(str(row.get("name") or "").strip(), cfg.aliases)
            if step not in wanted:
                continue
            started = _parse_stamp(row.get("start_stamp"))
            if started is None or started < cutoff:
                continue
            prev = found.get(step)
            if prev and prev["_started"] > started:
                continue
            found[step] = {
                "_started": started,
                "status": pc.DONE if _row_success(row) else pc.FAILED,
                "file": item["file"],
                "project_code": item.get("project_code") or "",
                "workdir": item.get("workdir") or "",
                "desc": (row.get("desc") or "")[:500],
                "started": row.get("start_stamp"),
                "finished": row.get("end_stamp"),
            }
    for value in found.values():
        value.pop("_started", None)
    return found


def step_durations(root: str | Path) -> dict[str, float]:
    """Median seconds per step over every result CSV in the project (for ETAs)."""
    from alfrd.avica_layout import layout_patterns, resolve_config, resolve_dir, result_csvs

    base = Path(root).resolve()
    try:
        cfg = resolve_config(base)
        target_dir = resolve_dir(base, cfg.get("target_dir")) or base / "reductions"
        items = result_csvs(base, target_dir, layout_patterns(base))
    except Exception:  # noqa: BLE001
        return {}
    samples: dict[str, list[float]] = {}
    for item in items:
        try:
            with open(base / item["file"], newline="", encoding="utf-8-sig") as stream:
                for row in csv.DictReader(stream):
                    start, end = _parse_stamp(row.get("start_stamp")), _parse_stamp(row.get("end_stamp"))
                    if start and end and end >= start:
                        samples.setdefault(str(row.get("name") or ""), []).append((end - start).total_seconds())
        except OSError:
            continue
    return {k: float(statistics.median(v)) for k, v in samples.items() if v}


# ---------------------------------------------------------------------------
# Creating, starting and controlling plans


def resolve_csv(cfg: ExecutionConfig, csv_file: str | Path | None = None) -> Path:
    """The plan CSV path: ``csv_file`` (default ``execution.plan_csv``), a relative one against the project root.

    Falls back to the current directory only when the file exists there and not in the root.
    """
    path = Path(csv_file) if csv_file else cfg.plan_csv
    if path.is_absolute():
        return path
    if not (cfg.root / path).exists() and (Path.cwd() / path).exists():
        return Path.cwd() / path
    return cfg.root / path


def create_plan(root: str | Path, csv_file: str | Path | None = None, *, mode: str | None = None,
                concurrency: int | None = None, on_failure: str | None = None,
                status_from: str | None = None, retry_failed: bool = False, target: str | None = None,
                treatment: str = "baseline", run_kind: str = "production", retry_of: str | None = None) -> PlanDir:
    """``retry_of`` names an earlier plan whose unaccepted turns this plan retries (lineage);
    ``retry_failed`` without it retries the latest earlier plan on the same CSV and target."""
    for label, value in (("treatment", treatment), ("run_kind", run_kind)):
        if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,39}", value):
            raise ExecutionError(f"{label} must be a short lowercase label")
    cfg = load_execution(root)
    if target is None and any(step.iterations for step in cfg.steps):
        selected = table_for(cfg, resolve_csv(cfg, csv_file)).rows
        if len(selected) != 1:
            raise ExecutionError("agent loops need exactly one task row selected; specify target")
        target = selected[0].target
    if target is not None and any("{target}" in v for step in cfg.steps for v in step.handoff.values()):
        from alfrd.agent_loop import validate_target
        target = validate_target(target)
        from alfrd.execution import load_task_execution
        cfg = load_task_execution(root, target, cap=True)
    path = resolve_csv(cfg, csv_file)
    if not path.exists():
        raise ExecutionError(f"plan CSV {path} does not exist (create one with `alfrd plan new`)")
    busy = active_plan(cfg.root, path, target)
    if busy and (target is not None or PlanDir(cfg.root, busy["id"]).runner_alive()):
        raise ExecutionError(f"plan {busy['id']} is already running on {path.name}; pause or cancel it first")
    settings = dict(cfg.settings)
    if mode:
        settings["mode"] = mode
    if concurrency:
        settings["concurrency"] = max(1, int(concurrency))
    if on_failure:
        settings["on_failure"] = on_failure
    if status_from:
        settings["status_from"] = status_from
    table = table_for(cfg, path)
    if target is not None:
        table.rows = [r for r in table.rows if r.target == target]
        if not table.rows:
            raise ExecutionError(f"unknown task target: {target}")
    loop = next((s for s in cfg.steps if s.iterations), None)
    if loop:
        if settings["mode"] != "step" or settings["concurrency"] != 1 or settings["on_failure"] != "stop_plan" or settings["status_from"] != "exit_code":
            raise ExecutionError("agent loops require step mode, concurrency 1, stop_plan and exit_code status")
        if len(table.rows) != 1 or table.steps != cfg.step_ids or any(table.rows[0].cell(s) == "skip" for s in table.steps):
            raise ExecutionError("agent loops need exactly one task row selected and every repeated step selected")
    if not table.steps:
        raise ExecutionError(f"{path.name} has no step columns ({', '.join(cfg.step_ids)})")
    if retry_of is not None and not PlanDir(cfg.root, retry_of).plan_file.is_file():
        raise ExecutionError(f"unknown plan to retry: {retry_of}")
    if retry_failed:
        if retry_of is None:
            earlier = next((p for p in list_plans(cfg.root) if Path(str(p.get("csv") or "")).name == path.name
                            and (target is None or not p.get("targets") or target in p["targets"])), None)
            retry_of = earlier["id"] if earlier else None
        reset_failed(cfg, path, target)
    workspace: dict[str, Any] = {}
    if loop and cfg.loop_workspace == "worktree" and any("{target}" in v for step in cfg.steps for v in step.handoff.values()):
        from alfrd import workspaces

        try:
            made = workspaces.ensure(cfg.root, table.rows[0].target)
        except ValueError as exc:
            raise ExecutionError(str(exc)) from exc
        cwd = workspaces.agent_cwd(cfg.root, table.rows[0].target) if made.get("workspace") else None
        workspace = {"workspace": str(cwd)} if cwd else {"workspace_warning": made.get("warning")}
    plan_id = datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{os.getpid() % 10000:04d}"
    if loop:
        plan_id += "-" + uuid.uuid4().hex[:8]
    folder = PlanDir(cfg.root, plan_id)
    try:
        rel = str(path.resolve().relative_to(cfg.root))
    except ValueError:
        rel = str(path.resolve())
    folder.save({
        "id": plan_id,
        "created": now_iso(),
        "root": str(cfg.root),
        "csv": rel,
        "workflow": cfg.workflow,
        **({"targets": [r.target for r in table.rows], "target": table.rows[0].target} if loop else {}),
        "steps": table.steps,
        "mode": settings["mode"],
        "concurrency": settings["concurrency"],
        "on_failure": settings["on_failure"],
        "status_from": settings["status_from"],
        "auto_resume": settings["auto_resume"],
        "launcher": settings["launcher"],
        "kill_grace": settings.get("kill_grace", 30),
        "heartbeat": settings.get("heartbeat", 10),
        "serialize_on": list(settings.get("serialize_on") or []),
        "serialize_match": settings.get("serialize_match") or "all",
        "usage_interval": settings.get("usage_interval", 5),
        "max_runtime": settings.get("max_runtime"),
        "status": "running",
        "treatment": treatment,
        "run_kind": run_kind,
        **workspace,
        **({"retry_of": retry_of} if retry_of else {}),
        "runner": {},
        "history": [{"at": now_iso(), "event": "created"}],
        **({"loop": {"iterations": loop.iterations, "unit": cfg.loop_unit, "definition": cfg.to_dict(), "manifest_sha256": manifest_hash(cfg.root)}} if loop else {}),
    })
    folder.set_control("run")
    return folder


def add_row(cfg: ExecutionConfig, path: Path, *, target: str, files: str = "", code: str = "",
            workdir: str = "", selected: Sequence[str] | None = None, steps: Sequence[str] | None = None,
            create: bool = False) -> pc.PlanTable:
    """Append one row to a plan CSV; safe to call while its plan is running, paused or finished.

    Same lock (keyed by CSV file name, like ``reset_failed``) and atomic
    replace the runner itself uses, so a live runner picks the row up on its
    next re-read. ``steps`` are the plan's step ids (default ``cfg.step_ids``);
    pass the plan's own list so the new row matches what the runner reads.
    Raises ``pc.DuplicateRowError`` for a duplicate (target, code) and
    ``FileNotFoundError`` for a missing CSV unless ``create``.
    """
    lock = cfg.root / ".alfrd" / "locks" / f"{Path(path).name}.lock"
    return pc.add_row(path, lock, list(steps or cfg.step_ids), target=target, files=files, code=code,
                      workdir=workdir, selected=selected, create=create, **_columns(cfg))


def reset_failed(cfg: ExecutionConfig, path: Path, target: str | None = None) -> int:
    """failed / blocked / interrupted / cancelled cells → todo."""
    table = table_for(cfg, path)
    cells = {(r.key, s): pc.TODO for r in table.rows for s in table.steps if (target is None or r.target == target) and r.cell(s) in pc.STOP_VALUES}
    if cells:
        lock = cfg.root / ".alfrd" / "locks" / f"{path.name}.lock"
        pc.update(path, lock, cfg.step_ids, cells, **_columns(cfg))
    return len(cells)


ORIG_PYTHONPATH = "ALFRD_ORIG_PYTHONPATH"


def _alfrd_env(base: Mapping[str, str]) -> dict[str, str]:
    """Environment for ALFRD's own processes (runner, shim): this ALFRD importable.

    The user's PYTHONPATH is remembered in ALFRD_ORIG_PYTHONPATH and the shim
    restores it for the command, so AVICA/CASA never see ALFRD's paths.
    """
    env = dict(base)
    if ORIG_PYTHONPATH not in env:
        env[ORIG_PYTHONPATH] = env.get("PYTHONPATH", "")
    src = str(Path(__file__).resolve().parents[2])
    parts = [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p and p != src]
    env["PYTHONPATH"] = os.pathsep.join([src, *parts])
    return env


def runner_command(folder: PlanDir) -> list[str]:
    return [sys.executable, "-m", "alfrd.cli", "plan", "runner", "--root", str(folder.root), folder.id]


def spawn_runner(folder: PlanDir, launcher: str | None = None) -> int | None:
    """Start the runner detached (new session). Returns its pid when known."""
    plan = folder.load()
    launcher = launcher or plan.get("launcher") or "detach"
    folder.path.mkdir(parents=True, exist_ok=True)
    command = runner_command(folder)
    env = _alfrd_env(os.environ)
    if launcher == "systemd-run" and shutil.which("systemd-run"):
        unit = f"alfrd-plan-{_slug(folder.id)}"
        command = ["systemd-run", "--user", "--collect", f"--unit={unit}", f"--working-directory={folder.root}",
                   f"--setenv=PYTHONPATH={env['PYTHONPATH']}", *command]
    # Stamp the spawn *before* starting the process: until this runner records
    # its own "started" (>= this stamp), reconcile must not mistake it for a dead
    # one. Stamping after Popen raced a fast runner: its "started" could land in
    # an earlier second (runner looked "starting" for 30 s, so reconcile skipped
    # the plan), and re-saving the plan could overwrite what the runner wrote.
    # The previous runner's "started" is cleared so it cannot count for this one.
    previous = dict(plan.get("runner") or {})
    plan["runner"] = {**previous, "spawned": now_iso(), "started": None}
    folder.save(plan)
    log = open(folder.runner_log, "a", encoding="utf-8")
    try:
        kwargs: dict[str, Any] = {"start_new_session": True} if os.name != "nt" else {
            "creationflags": getattr(subprocess, "DETACHED_PROCESS", 0)}
        process = subprocess.Popen(command, cwd=str(folder.root), env=env, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT, **kwargs)
    except BaseException:
        plan = folder.load()
        plan["runner"] = previous  # nothing was started: do not hold reconcile off
        folder.save(plan)
        raise
    finally:
        log.close()
    return process.pid


def _starting(plan: Mapping[str, Any], grace: float = 30.0) -> bool:
    """A runner was spawned moments ago and has not started yet."""
    runner = plan.get("runner") or {}
    spawned = _parse_stamp(runner.get("spawned"))
    if spawned is None or (datetime.now() - spawned).total_seconds() > grace:
        return False
    started = _parse_stamp(runner.get("started"))
    return started is None or started < spawned


def control(root: str | Path, plan_id: str, action: str, *, retry_failed: bool = False, spawn: bool = True) -> dict[str, Any]:
    """pause / cancel / resume a plan. Resume starts a runner when none is alive."""
    folder = PlanDir(Path(root).resolve(), plan_id)
    plan = folder.load()
    if action == "resume":
        if retry_failed:
            reset_failed(load_execution(folder.root), folder.csv_path(plan))
        folder.set_control("run")
        if not folder.runner_alive():
            plan["status"] = "running"
            plan.setdefault("history", []).append({"at": now_iso(), "event": "resumed"})
            folder.save(plan)
            if spawn:
                spawn_runner(folder)
        return folder.load()
    if action in ("pause", "cancel"):
        folder.set_control(action)
        if not folder.runner_alive():
            if action == "cancel" and any(u.get("status") == "running" for u in folder.units()) and spawn:
                spawn_runner(folder)  # it kills and finalizes what is still alive
            elif plan.get("status") in ACTIVE + ("interrupted",):
                plan["status"] = "paused" if action == "pause" else "cancelled"
                plan.setdefault("history", []).append({"at": now_iso(), "event": action})
                folder.save(plan)
        return folder.load()
    raise ExecutionError(f"unknown action {action!r}")


# ---------------------------------------------------------------------------
# The runner


OVERRIDE_KEYS = {"human_review", "manual", "after", "model"}


def apply_overrides(cfg: ExecutionConfig, overrides: Mapping[str, Mapping[str, Any]]) -> ExecutionConfig:
    """The configuration with a plan's per-step overrides (unknown steps are ignored)."""
    if not overrides:
        return cfg
    from dataclasses import replace

    from alfrd.agent_io import adapter_for, agent_command
    from alfrd.execution import parse_delay

    steps = []
    for step in cfg.steps:
        change = overrides.get(step.id) or {}
        updates: dict[str, Any] = {}
        if isinstance(change.get("human_review"), bool):
            updates["human_review"] = change["human_review"]
        if isinstance(change.get("manual"), bool):
            updates["manual"] = change["manual"]
        if "after" in change:
            with contextlib.suppress(ValueError):
                updates["after"] = parse_delay(change["after"])
        if isinstance(change.get("model"), str) and change["model"].strip() and step.argv:
            argv, model, _ = agent_command(step.argv, change["model"].strip(), adapter=adapter_for(name=step.adapter, model_option=step.model_option))
            updates.update(argv=argv, model=model or change["model"].strip())
        steps.append(replace(step, **updates) if updates else step)
    return replace(cfg, steps=steps)


def set_override(root: str | Path, plan_id: str, step: str, changes: Mapping[str, Any]) -> dict[str, Any]:
    """Change a step of a running plan: review, human-written turn, delay or model.

    Review may be switched while the turn runs (until its handoff is published);
    the other settings only for turns that have not started. ``None`` clears a setting.
    """
    from alfrd.execution import parse_delay

    folder = PlanDir(Path(root).resolve(), plan_id)
    plan = folder.load()
    if step not in (plan.get("steps") or []):
        raise ExecutionError(f"step {step!r} is not part of plan {plan_id}")
    unknown = set(changes) - OVERRIDE_KEYS
    if unknown or not changes:
        raise ExecutionError(f"overrides accept {', '.join(sorted(OVERRIDE_KEYS))}")
    for key in ("human_review", "manual"):
        if changes.get(key) is not None and not isinstance(changes[key], bool):
            raise ExecutionError(f"{key} must be true or false")
    if changes.get("after") is not None:
        try:
            parse_delay(changes["after"])
        except ValueError as exc:
            raise ExecutionError(str(exc)) from exc
    if changes.get("model") is not None and (not isinstance(changes["model"], str) or not changes["model"].strip()):
        raise ExecutionError("model must be a nonempty string")
    attempts = [u for u in folder.units() if step in (u.get("steps") or [])]
    latest = attempts[-1] if attempts else None
    if latest and (latest.get("artifact") or latest.get("status") == "done"):
        raise ExecutionError(f"{step} already finished; its settings can no longer change")
    if latest and latest.get("status") == "running" and set(changes) - {"human_review"}:
        raise ExecutionError(f"{step} is running; only its review can still change")
    with pc.locked(folder.path / "overrides.lock"):
        current = folder.overrides()
        merged = {**current.get(step, {}), **changes}
        merged = {k: v for k, v in merged.items() if v is not None}
        if merged:
            current[step] = merged
        else:
            current.pop(step, None)
        _write_json(folder.overrides_file, current)
    return current


@dataclass
class Live:
    unit: dict[str, Any]
    process: subprocess.Popen | None = None      # None when adopted from an earlier runner
    started: float = field(default_factory=time.time)
    last_progress: float = 0.0
    killed_at: float | None = None
    reason: str | None = None


class Runner:
    def __init__(self, root: str | Path, plan_id: str) -> None:
        self.folder = PlanDir(Path(root).resolve(), plan_id)
        self.plan = self.folder.load()
        self.live: dict[str, Live] = {}
        self.started_rows: set[str] = set()  # rows that ran a unit in this plan (they hold their keys)
        self.stop_new = False
        self.host = socket.gethostname()
        self._cfg: ExecutionConfig | None = None
        self._cfg_key: tuple | None = None
        self.delayed: list[dict[str, Any]] = []  # rows whose next step waits for its ``after`` delay

    # -- helpers ---------------------------------------------------------
    def log(self, text: str) -> None:
        print(f"{now_iso()} {text}", flush=True)

    @property
    def cfg(self) -> ExecutionConfig:
        from alfrd.manifest_default import local_manifest

        path = local_manifest(self.folder.root)
        overrides = self.folder.overrides_file
        key = (str(path), path.stat().st_mtime_ns if path and path.exists() else None,
               overrides.stat().st_mtime_ns if overrides.exists() else None)
        if self._cfg is None or key != self._cfg_key:
            cfg = load_execution(self.folder.root, iterations=self.plan.get("loop", {}).get("iterations"))
            self._cfg, self._cfg_key = apply_overrides(cfg, self.folder.overrides()), key
        return self._cfg

    @property
    def csv_file(self) -> Path:
        return self.folder.csv_path(self.plan)

    def table(self) -> pc.PlanTable:
        table = table_for(self.cfg, self.csv_file, self.plan.get("steps"))
        if self.plan.get("targets"):
            table.rows = [r for r in table.rows if r.target in self.plan["targets"]]
        return table

    def set_cells(self, cells: Mapping[tuple[str, str], str], *, only_if: Mapping[tuple[str, str], set[str]] | None = None,
                  fills: Mapping[tuple[str, str], str] | None = None) -> None:
        pc.update(self.csv_file, self.folder.csv_lock(self.plan), self.plan.get("steps") or self.cfg.step_ids,
                  cells, fills, only_if=only_if, **_columns(self.cfg))

    def save_plan(self, **changes: Any) -> None:
        current = self.folder.load()
        runner = {**(current.get("runner") or {}), **(changes.get("runner") or {})}
        current.update(changes)
        current["runner"] = runner
        self.plan = current
        self.folder.save(current)

    def event(self, text: str) -> None:
        current = self.folder.load()
        history = (current.get("history") or [])[-199:]
        history.append({"at": now_iso(), "event": text})
        current["history"] = history
        self.plan = current
        self.folder.save(current)

    # -- main loop -------------------------------------------------------
    def run(self) -> int:
        self.folder.path.mkdir(parents=True, exist_ok=True)
        with open(self.folder.lock_file, "a+") as lock:
            if fcntl is not None:
                try:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    self.log("another runner holds this plan; exiting")
                    return 3
            if self.plan.get("loop"):
                workspace = self.cfg.cwd
                scoped = any("{target}" in v for step in self.cfg.steps for v in step.handoff.values())
                guard_name = f".alfrd-agent-loop-{_slug(self.plan.get('target', 'task'))}.lock" if scoped else ".alfrd-agent-loop.lock"
                guard = workspace / guard_name
                if self.plan.get("workspace"):
                    # A task worktree: one loop per worktree; the lock stays out of the checkout.
                    from alfrd.agent_loop import validate_target

                    guard = self.folder.root / validate_target(self.plan.get("target", "task")) / ".alfrd-agent-loop.lock"
                with open(guard, "a+") as workspace_lock:
                    if fcntl is not None:
                        cancel_started = None
                        while True:
                            try:
                                fcntl.flock(workspace_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                                break
                            except OSError:
                                if any(u.get("status") == "running" and pid_alive(u.get("pid"), u.get("proc_start"), u.get("host")) for u in self.folder.units()):
                                    created = _parse_stamp(self.plan.get("created"))
                                    expired = self.plan.get("max_runtime") and created and (datetime.now() - created).total_seconds() >= self.plan["max_runtime"]
                                    if self.folder.control() == "cancel" or expired:
                                        cancel_started = cancel_started or time.time()
                                        sig = signal.SIGKILL if time.time() - cancel_started > float(self.plan.get("kill_grace") or 30) else signal.SIGTERM
                                        for unit in self.folder.units():
                                            if unit.get("status") == "running" and pid_alive(unit.get("pid"), unit.get("proc_start"), unit.get("host")):
                                                self._signal(Live(unit=unit), sig)
                                    time.sleep(0.1)
                                    continue
                                self.save_plan(status="interrupted", error="another agent loop owns this workspace")
                                return 3
                    # Commands retain this descriptor after runner death. An adopted
                    # runner waits for them to finish before taking the workspace lock.
                    self.workspace_fd = workspace_lock.fileno() if fcntl is not None else None
                    return self._run()
            return self._run()

    def _run(self) -> int:
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        stop = {"flag": False}

        def on_term(_sig, _frame):
            stop["flag"] = True

        signal.signal(signal.SIGTERM, on_term)
        signal.signal(signal.SIGINT, on_term)
        self.save_plan(status="running", runner={
            "pid": os.getpid(), "host": self.host, "proc_start": proc_start(os.getpid()),
            "started": now_iso(), "heartbeat": now_iso(), "stopped": None,
        })
        self.event(f"runner {os.getpid()} on {self.host} started")
        self.log(f"plan {self.folder.id}: {self.csv_file} mode={self.plan['mode']} concurrency={self.plan['concurrency']}")
        self.adopt()
        beat = float(self.plan.get("heartbeat") or 10)
        last_beat = 0.0
        final = "finished"
        while True:
            if time.time() - last_beat >= beat:
                self.save_plan(runner={"heartbeat": now_iso()}, counts=self.counts())
                last_beat = time.time()
            if stop["flag"]:
                # The runner is asked to stop (not the plan): leave commands running;
                # the next runner re-adopts them.
                final = "running"
                break
            action = self.folder.control()
            self.poll()
            deadline = self.plan.get("max_runtime")
            created = _parse_stamp(self.plan.get("created"))
            if deadline and created and (datetime.now() - created).total_seconds() >= deadline:
                self.stop_new = True
                self.save_plan(error="total runtime limit reached")
                for live in self.live.values():
                    if not live.killed_at:
                        self.kill(live, "total runtime limit reached")
            if action == "cancel":
                self.cancel_all()
                final = "cancelled"
                break
            if action == "pause" or self.stop_new:
                if not self.live:
                    final = "paused" if action == "pause" else "failed"
                    break
            else:
                started = self.schedule()
                if not self.live and not started and not self.delayed:
                    final = "failed" if self.stop_new else "finished"
                    break
            time.sleep(POLL)
        counts = self.counts()
        self.save_plan(status=final, counts=counts, waiting=[], runner={"stopped": now_iso(), "heartbeat": now_iso()})
        self.event(f"runner stopped: {final} ({', '.join(f'{k} {v}' for k, v in counts.items() if v)})")
        self.log(f"stopped: {final}")
        return 0

    def counts(self) -> dict[str, int]:
        try:
            table = self.table()
        except Exception:  # noqa: BLE001
            return {}
        out: dict[str, int] = {}
        for row in table.rows:
            for step in table.steps:
                value = row.cell(step)
                if value != "skip":
                    out[value] = out.get(value, 0) + 1
        return out

    # -- scheduling ------------------------------------------------------
    def _busy_rows(self) -> set[str]:
        keys: set[str] = set()
        for live in self.live.values():
            keys.update(live.unit.get("rows") or [live.unit.get("row")])
        return keys

    def schedule(self) -> int:
        self.delayed = []
        if self.plan.get("loop"):
            if self.plan["loop"]["manifest_sha256"] not in (manifest_hash(self.folder.root), manifest_hash(self.folder.root, legacy=True)):
                self.stop_new = True
                self.save_plan(error="agent workflow changed; restore its manifest before resuming")
                return 0
            table = self.table()
            if len(table.rows) != 1 or any(table.rows[0].cell(s) == "skip" for s in table.steps):
                self.stop_new = True
                self.save_plan(error="agent loops require one task and all turns; restore the plan CSV")
                return 0
        mode = self.plan["mode"]
        limit = int(self.plan.get("concurrency") or 1)
        if len(self.live) >= limit:
            return 0
        table = self.table()
        if mode == "batch":
            if self.live:
                return 0
            rows = [r for r in table.rows if any(r.cell(s) == pc.TODO for s in table.steps)]
            if not rows:
                return 0
            steps = [s for s in table.steps if any(r.cell(s) == pc.TODO for r in rows)]
            return self.launch(UnitSpec(rows, steps, mode), table)
        names, match = serialize_settings(self.plan, self.cfg)
        picked = pick_rows(
            table, mode=mode, on_failure=self.plan["on_failure"], limit=limit, free=limit - len(self.live),
            names=names, match=match, base=self.cfg.cwd, running_rows=self._busy_rows(),
            started_rows=self.started_rows, locks={self._lock_key(l.unit) for l in self.live.values()} - {None},
        )
        if not picked.start and not self.live and picked.waiting:
            # Safety net: nothing runs, yet rows wait on each other. Release the holds rather
            # than end the plan with cells still to do.
            self.log("rows wait on each other with nothing running; releasing their holds")
            self.started_rows.clear()
            picked = pick_rows(table, mode=mode, on_failure=self.plan["on_failure"], limit=limit, free=limit,
                               names=names, match=match, base=self.cfg.cwd)
        started = 0
        delayed = []
        units = self.folder.units() if any(self.cfg.step(spec.steps[0]).after for _row, spec in picked.start if spec.mode == "step") else []
        for row, spec in picked.start:
            until = self._not_before(row, spec, table, units)
            if until is not None:
                delayed.append({"row": row.key, "target": row.target, "code": row.code, "kind": "delay",
                                "step": spec.steps[0], "until": until.isoformat(timespec="seconds"),
                                "reason": f"waiting: {spec.steps[0]} starts after {until:%Y-%m-%d %H:%M:%S}"})
                continue
            self.started_rows.add(row.key)
            started += self.launch(spec, table)
        self.delayed = delayed
        # A row with nothing left to run holds nothing (and holds again only once it starts anew).
        busy = self._busy_rows()
        self.started_rows = {k for k in self.started_rows if k in busy or (
            (r := table.row(k)) is not None and unit_for_row(r, table.steps, mode, self.plan["on_failure"]))}
        # Rows waiting on a conflict are only "waiting" while something they wait for still runs.
        waiting = picked.waiting + delayed
        if waiting != (self.plan.get("waiting") or []):
            self.save_plan(waiting=waiting)
        return started

    def _not_before(self, row: pc.PlanRow, spec: UnitSpec, table: pc.PlanTable,
                    units: Sequence[Mapping[str, Any]]) -> datetime | None:
        """When a step with ``after`` may start: its previous step's finish + the delay; None if now."""
        if spec.mode != "step":
            return None
        step = spec.steps[0]
        delay = self.cfg.step(step).after
        steps = list(table.steps)
        if not delay or step not in steps or steps.index(step) == 0:
            return None
        previous = steps[steps.index(step) - 1]
        finished = [_parse_stamp(u.get("finished")) for u in units
                    if previous in (u.get("steps") or []) and row.key in (u.get("rows") or [u.get("row")]) and u.get("status") == "done"]
        finished = [f for f in finished if f]
        if not finished:
            return None
        until = max(finished) + timedelta(seconds=delay)
        return until if datetime.now() < until else None

    @staticmethod
    def _lock_key(unit: Mapping[str, Any]) -> str | None:
        return f"{unit['code']}/{unit.get('workdir') or ''}" if unit.get("code") else None

    def _new_unit_id(self, spec: UnitSpec) -> str:
        n = len(list(self.folder.units_dir.glob("*.json"))) + 1 if self.folder.units_dir.is_dir() else 1
        label = spec.steps[0] if spec.mode == "step" else spec.mode
        return f"{n:04d}-{_slug(spec.row_key)}-{_slug(label)}"

    def _fallback_model(self, step: Any, unit: dict[str, Any]) -> str | None:
        """The next ``fallback_models`` entry when the attempt this unit retries asked for one."""
        if not unit.get("retry_of") or not step.fallback_models:
            return None
        plan_id, _, unit_id = str(unit["retry_of"]).partition("/")
        previous = next((u for u in PlanDir(self.folder.root, plan_id).units() if u.get("id") == unit_id), None)
        index = previous.get("fallback_next") if previous else None
        if not isinstance(index, int) or not 0 <= index < len(step.fallback_models):
            return None
        unit["fallback_index"] = index
        return step.fallback_models[index]

    def _lineage(self, spec: UnitSpec) -> dict[str, Any]:
        """Logical turn identity: retries inherit it, fresh work gets a new one.

        A retry is an earlier unaccepted attempt at the same (row, step) in this
        plan, or, in a plan created with ``retry_of``, in that earlier plan.
        """
        def latest(units: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
            same = [u for u in units if u.get("row") == spec.row_key and list(u.get("steps") or []) == list(spec.steps)
                    and u.get("logical_turn_id")]
            return same[-1] if same else None

        previous = latest(self.folder.units())
        if previous is None and self.plan.get("retry_of"):
            previous = latest(PlanDir(self.folder.root, self.plan["retry_of"]).units())
        if previous and previous.get("outcome") != "accepted" and previous.get("status") in ("failed", "cancelled", "interrupted"):
            return {"logical_turn_id": previous["logical_turn_id"],
                    "attempt_number": int(previous.get("attempt_number") or 1) + 1,
                    "retry_of": f"{previous.get('plan')}/{previous.get('id')}"}
        return {"logical_turn_id": uuid.uuid4().hex, "attempt_number": 1, "retry_of": None}

    def launch(self, spec: UnitSpec, table: pc.PlanTable) -> int:
        row = spec.rows[0]
        unit: dict[str, Any] = {
            "id": self._new_unit_id(spec),
            "plan": self.folder.id,
            "mode": spec.mode,
            "row": spec.row_key,
            "rows": [r.key for r in spec.rows],
            "target": spec.target,
            "code": row.code if spec.mode != "batch" else "",
            "workdir": row.workdir if spec.mode != "batch" else "",
            "steps": spec.steps,
            "status": "running",
            "started": now_iso(),
            "host": self.host,
            "results": {},
            "schema": 3,  # 2: usage / usage_file after the command ends; 3: lineage, labels, outcome
            "treatment": self.plan.get("treatment"),
            "run_kind": self.plan.get("run_kind"),
            **self._lineage(spec),
        }
        cells_rows = spec.rows
        try:
            step = self.cfg.step(spec.steps[0]) if spec.mode == "step" else None
            values = values_for(self.cfg, spec, {**self.plan, "unit_id": unit["id"]}, self.csv_file)
            if step and step.handoff:
                from alfrd.agent_loop import prepare

                archive = self.folder.path / "handoffs" / unit["id"]
                unit["handoff"] = prepare(self.folder.root, archive, step, self.folder.id, unit["id"], target=values["target"])
                unit.update(iteration=step.iteration, agent=step.entrypoint or step.base_step, manual=step.manual,
                            human_review=step.human_review, requested_model=step.model, adapter=step.adapter,
                            review_gate="runner", turn=step.turn, roles=unit["handoff"].get("roles", []))
            argv, env, timeout = command_for(self.cfg, spec, {**self.plan, "unit_id": unit["id"]}, self.csv_file)
            fallback = self._fallback_model(step, unit) if step else None
            if fallback:
                from alfrd.agent_io import adapter_for, agent_command

                argv = list(agent_command(argv, fallback, adapter=adapter_for(name=step.adapter, model_option=step.model_option))[0])
                unit.update(requested_model=fallback)
                self.event(f"{unit['id']}: retrying with fallback model {fallback}")
            if step and step.manual:
                argv = [sys.executable, "-c", "import pathlib,sys,time; p=pathlib.Path(sys.argv[1]);\nwhile not p.is_file(): time.sleep(0.2)", values["response_file"]]
        except (ExecutionError, OSError, ValueError) as exc:
            unit.update(status="failed", error=str(exc), finished=now_iso(),
                        outcome="failed_runtime", outcome_reason=f"cannot start: {exc}")
            self.folder.save_unit(unit)
            self.log(f"{unit['id']}: cannot start: {exc}")
            first = spec.steps[0]
            self.set_cells({(r.key, first): pc.FAILED for r in cells_rows}, only_if={(r.key, first): {pc.TODO} for r in cells_rows})
            self.after_failure(spec.rows, first)
            return 0
        log_path = self.folder.logs_dir / f"{unit['id']}.log"
        exit_path = self.folder.logs_dir / f"{unit['id']}.exit"
        self.folder.logs_dir.mkdir(parents=True, exist_ok=True)
        cwd = Path(self.plan["workspace"]) if self.plan.get("workspace") else self.cfg.cwd
        if step and step.cwd:
            cwd = Path(render([step.cwd], values)[0]).expanduser()
            cwd = cwd if cwd.is_absolute() else self.folder.root / cwd
        unit.update(argv=argv, cwd=str(cwd), log=str(log_path.relative_to(self.folder.root)),
                    exit_file=str(exit_path.relative_to(self.folder.root)), timeout=timeout,
                    usage_file=str(exit_path.with_suffix(".usage.jsonl").relative_to(self.folder.root)))
        process_env = os.environ.copy()
        process_env.update({str(k): str(v) for k, v in (self.cfg.settings.get("env") or {}).items()})
        process_env.update(env)
        process_env.update({"ALFRD_PLAN_ID": self.folder.id, "ALFRD_TARGET": spec.target,
                            "ALFRD_STEPS": ",".join(spec.steps), "ALFRD_ROOT": str(self.folder.root),
                            "ALFRD_USAGE_INTERVAL": str(self.plan.get("usage_interval", self.cfg.settings.get("usage_interval", 5))),
                            "ALFRD_LAUNCHER": str(self.plan.get("launcher") or "detach"),
                            "ALFRD_UNIT": unit["id"]})  # marks every process of this command (usage)
        usage_dir = self._workdir_path(spec)
        if usage_dir:
            process_env["ALFRD_USAGE_DIR"] = usage_dir
        wanted = {**(self.cfg.settings.get("env") or {}), **env}
        if "PYTHONPATH" in wanted:  # alfrd.yaml sets the command's PYTHONPATH itself
            process_env[ORIG_PYTHONPATH] = str(wanted["PYTHONPATH"])
        process_env = _alfrd_env(process_env)
        with open(log_path, "a", encoding="utf-8") as log:
            log.write(f"# alfrd plan {self.folder.id} · {spec.target or 'batch'} · {', '.join(spec.steps)} · {now_iso()}\n")
            log.write(f"# cwd {cwd}\n$ {' '.join(argv)}\n")
            log.flush()
            try:
                shim_args = []
                if step and step.stdin_file:
                    stdin_path = Path(render([step.stdin_file], values)[0])
                    stdin_path = stdin_path if stdin_path.is_absolute() else self.folder.root / stdin_path
                    shim_args += ["--stdin-file", str(stdin_path)]
                if step and step.output_file:
                    output_path = Path(render([step.output_file], values)[0])
                    output_path = output_path if output_path.is_absolute() else self.folder.root / output_path
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    if output_path.exists():
                        raise ExecutionError("response file already exists; refusing to reuse a stale response")
                    if step.output_capture == "stdout" and not step.manual:
                        shim_args += ["--stdout-file", str(output_path)]
                    if step.claude_stream and not step.manual:
                        if step.output_capture != "stdout":
                            shim_args += ["--stdout-file", str(output_path)]
                        shim_args += ["--claude-stream"]
                workspace_fd = getattr(self, "workspace_fd", None)
                process = subprocess.Popen(
                    [sys.executable, "-m", "alfrd.runtime.shim", "--exit-file", str(exit_path), *shim_args, "--", *argv],
                    cwd=str(cwd), env=process_env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                    start_new_session=True,
                    pass_fds=(workspace_fd,) if workspace_fd is not None else (),
                )
            except (OSError, ExecutionError) as exc:
                unit.update(status="failed", error=str(exc), finished=now_iso(),
                            outcome="failed_runtime", outcome_reason=f"cannot start: {exc}")
                self.folder.save_unit(unit)
                first = spec.steps[0]
                self.set_cells({(r.key, first): pc.FAILED for r in cells_rows})
                self.after_failure(spec.rows, first)
                return 0
        unit.update(pid=process.pid, pgid=process.pid, proc_start=proc_start(process.pid))
        self.folder.save_unit(unit)
        first = spec.steps[0]
        self.set_cells({(r.key, first): pc.RUNNING for r in cells_rows}, only_if={(r.key, first): {pc.TODO} for r in cells_rows})
        self.live[unit["id"]] = Live(unit=unit, process=process)
        self.log(f"{unit['id']}: started pid {process.pid}: {' '.join(argv)}")
        return 1

    def _workdir_path(self, spec: UnitSpec) -> str | None:
        """The row's AVICA work dir (``{target_dir}/{code}/{workdir}``) when it exists, for the size at start/end."""
        row = spec.rows[0] if spec.mode != "batch" else None
        if row is None or not row.code or not row.workdir:
            return None
        try:
            from alfrd.avica_layout import resolve_config, resolve_dir

            target_dir = resolve_dir(self.folder.root, resolve_config(self.folder.root).get("target_dir"))
            path = Path(target_dir) / row.code / row.workdir if target_dir else None
        except Exception:  # noqa: BLE001 - not an AVICA layout
            return None
        return str(path) if path and path.is_dir() else None

    # -- watching --------------------------------------------------------
    def _alive(self, live: Live) -> bool:
        if live.process is not None:
            return live.process.poll() is None
        unit = live.unit
        if pid_alive(unit.get("pid"), unit.get("proc_start"), unit.get("host")):
            return True
        child = _read_json(self.folder.root / (unit.get("exit_file", "") + "").replace(".exit", ".child"), {}) or {}
        return pid_alive(child.get("pid"), child.get("proc_start"), unit.get("host"))

    def _review_hold(self, live: Live) -> bool:
        """After a successful exit, hold the handoff until a person approves it.

        Whether a turn needs review is read when its command ends (alfrd.yaml plus
        this plan's overrides), so review can be switched on while it runs.
        """
        unit = live.unit
        if live.killed_at or not unit.get("handoff") or unit.get("review_gate") != "runner":
            return False
        exit_path = self.folder.root / str(unit.get("exit_file") or "-")
        exit_data = _read_json(exit_path, None)
        if not isinstance(exit_data, dict) or exit_data.get("exit_code") != 0:
            return False
        review, approval = exit_path.with_suffix(".review.json"), exit_path.with_suffix(".approval.json")
        rejection = exit_path.with_suffix(".rejection.json")
        if approval.exists():
            if unit.get("review_status") != "approved":
                unit.update(review_status="approved")
                self.folder.save_unit(unit)
                self.event(f"review approved: {unit['id']}")
            return False
        if rejection.exists():
            decision = _read_json(rejection, {}) or {}
            live.reason = "rejected in review" + (f": {decision['reason']}" if decision.get("reason") else "")
            unit.update(review_status="rejected", outcome="rejected", outcome_reason=decision.get("reason") or "rejected in review")
            self.folder.save_unit(unit)
            return False
        try:
            step = self.cfg.step(unit["steps"][0])
        except Exception:  # noqa: BLE001 - the step left the manifest
            return review.exists()
        if not step.human_review and not review.exists():
            return False
        if not review.exists():
            from alfrd.agent_loop import REQUIRED_HEADINGS, sha256, validate_response

            response = Path(unit["handoff"]["response_file"])
            try:
                text = response.read_text(encoding="utf-8")
            except OSError:
                return False
            if validate_response(text, unit["handoff"].get("headings", REQUIRED_HEADINGS)):
                return False  # finalize reports the invalid response
            _write_json(review, {"status": "pending", "hash": sha256(text)})
            unit.update(review_status="pending", human_review=True)
            self.folder.save_unit(unit)
            self.event(f"awaiting human review: {unit['id']}")
        return True

    def poll(self) -> None:
        for unit_id, live in list(self.live.items()):
            self.agent_metadata(live.unit)
            alive = self._alive(live)
            if not alive and self._review_hold(live):
                continue
            timeout = live.unit.get("timeout")
            if alive and timeout and not live.killed_at:
                started = _parse_stamp(live.unit.get("started"))
                if started and (datetime.now() - started).total_seconds() > float(timeout):
                    self.kill(live, f"timed out after {float(timeout):.0f} s")
            if alive and live.killed_at and time.time() - live.killed_at > float(self.plan.get("kill_grace") or 30):
                self._signal(live, signal.SIGKILL)
            if alive:
                if len(live.unit["steps"]) > 1 and time.time() - live.last_progress > 10:
                    live.last_progress = time.time()
                    self.progress(live)
                continue
            del self.live[unit_id]
            self.finalize(live.unit, process=live.process, reason=live.reason)

    def agent_metadata(self, unit: dict[str, Any]) -> None:
        if not unit.get("handoff"):
            return
        from alfrd.agent_io import codex_report, unit_adapter

        adapter = unit_adapter(unit)
        exit_path = self.folder.root / unit.get("exit_file", "-")
        metadata_path = exit_path.with_suffix(".agent.json")
        metadata = _read_json(metadata_path, {}) or {}
        if adapter.log_usage and exit_path.is_file() and not metadata_path.exists():
            text = ""
            try:
                with open(self.folder.root / unit["log"], "rb") as log:
                    log.seek(max(0, log.seek(0, 2) - 65536))
                    text = log.read().decode("utf-8", errors="replace")
            except OSError:
                pass
            usage, source = codex_report(text)
            metadata = {"models": [unit["model"]] if unit.get("model") else [], "usage": usage,
                        "usage_source": source, "total_cost_usd": None}
            _write_json(metadata_path, metadata)
        elif not metadata and exit_path.is_file() and not metadata_path.exists():
            metadata = {"usage": None, "usage_source": "unavailable", "total_cost_usd": None}
        changes = {"roles": unit["handoff"].get("roles", [])}
        if "usage" in metadata:
            changes.update(agent_usage=metadata["usage"], total_cost_usd=metadata.get("total_cost_usd"),
                           usage_source=metadata.get("usage_source") or ("unavailable" if not metadata["usage"] else None))
        if metadata.get("model"):
            changes.update(model=metadata["model"], models=metadata.get("models", []))
        elif adapter.name == "codex" and not unit.get("model"):
            try:
                with open(self.folder.root / unit["log"], encoding="utf-8") as log:
                    header = log.read(16384)
                match = re.search(r"^model:\s*(\S+)", header, re.M)
                if match:
                    changes["model"] = match.group(1)
            except OSError:
                pass
        review = _read_json(exit_path.with_suffix(".review.json"), {}) or {}
        if review.get("status") == "pending":
            changes["review_status"] = ("approved" if exit_path.with_suffix(".approval.json").exists() else
                                        "rejected" if exit_path.with_suffix(".rejection.json").exists() else "pending")
        if any(unit.get(k) != v for k, v in changes.items()):
            unit.update(changes)
            self.folder.save_unit(unit)

    def _signal(self, live: Live, sig: int) -> None:
        pgid = live.unit.get("pgid") or live.unit.get("pid")
        if not pgid:
            return
        with contextlib.suppress(OSError):
            os.killpg(int(pgid), sig)
        child = _read_json(self.folder.root / str(live.unit.get("exit_file", "")).replace(".exit", ".child"), {}) or {}
        if child.get("pid") and pid_alive(child.get("pid"), child.get("proc_start")):
            with contextlib.suppress(OSError):
                os.kill(int(child["pid"]), sig)

    def kill(self, live: Live, reason: str) -> None:
        live.reason = reason
        live.killed_at = time.time()
        self.log(f"{live.unit['id']}: stopping ({reason})")
        self._signal(live, signal.SIGTERM)

    def cancel_all(self) -> None:
        for live in self.live.values():
            if not live.killed_at:
                self.kill(live, "cancelled")
        grace = float(self.plan.get("kill_grace") or 30)
        deadline = time.time() + grace
        while self.live and time.time() < deadline:
            self.poll()
            time.sleep(0.5)
        for live in list(self.live.values()):
            self._signal(live, signal.SIGKILL)
        time.sleep(0.5)
        self.poll()
        for unit_id, live in list(self.live.items()):
            del self.live[unit_id]
            self.finalize(live.unit, process=live.process, reason="cancelled")

    def adopt(self) -> None:
        """Take over units an earlier runner left behind (and the rows they hold)."""
        table = self.table()
        running_rows = set()
        units = self.folder.units()
        self.started_rows.update(k for u in units if u.get("mode") != "batch" for k in (u.get("rows") or [u.get("row")]) if k)
        for unit in units:
            if unit.get("status") != "running":
                continue
            if unit.get("host") and unit["host"] != self.host:
                self.log(f"{unit['id']}: runs on {unit['host']}; not adopted")
                running_rows.update(unit.get("rows") or [])
                continue
            live = Live(unit=unit)
            if self._alive(live) or (self.folder.control() != "cancel" and self._review_hold(live)):
                self.live[unit["id"]] = live
                running_rows.update(unit.get("rows") or [unit.get("row")])
                self.log(f"{unit['id']}: re-adopted pid {unit.get('pid')}")
                self.event(f"re-adopted {unit['id']}")
            else:
                self.finalize(unit, process=None, reason="cancelled" if self.folder.control() == "cancel" else None, adopted=True)
        # Cells left "running" by a runner that died before writing a unit.
        stale = {(r.key, s): pc.INTERRUPTED for r in table.rows for s in table.steps
                 if r.cell(s) == pc.RUNNING and r.key not in running_rows}
        if stale:
            self.set_cells(stale, only_if={k: {pc.RUNNING} for k in stale})

    def progress(self, live: Live) -> None:
        """Multi-step units (mode target/batch): update cells as result rows appear."""
        unit = live.unit
        since = _parse_stamp(unit.get("started")) or datetime.now()
        cells: dict[tuple[str, str], str] = {}
        for key in unit.get("rows") or [unit["row"]]:
            row = self.table().row(key)
            if row is None:
                continue
            found = verify_steps(self.cfg, row.target, row.code, row.workdir, unit["steps"], since)
            nxt_running = False
            for step in unit["steps"]:
                if step in found:
                    cells[(key, step)] = found[step]["status"]
                elif not nxt_running:
                    cells[(key, step)] = pc.RUNNING
                    nxt_running = True
        if cells:
            self.set_cells(cells, only_if={k: {pc.TODO, pc.RUNNING} for k in cells})

    def finalize(self, unit: dict[str, Any], *, process: subprocess.Popen | None, reason: str | None,
                 adopted: bool = False) -> None:
        self.agent_metadata(unit)
        exit_data = _read_json(self.folder.root / str(unit.get("exit_file") or "-"), None)
        exit_code: int | None = None
        note = None
        if isinstance(exit_data, dict):
            exit_code = exit_data.get("exit_code")
        elif process is not None and process.returncode is not None:
            exit_code = process.returncode
            note = "the command's wrapper ended without an exit file"
        since = _parse_stamp(unit.get("started")) or datetime.now()
        status_from = self.plan.get("status_from") or "exit_code"
        cells: dict[tuple[str, str], str] = {}
        fills: dict[tuple[str, str], str] = {}
        failed_step = None
        interrupted = exit_code is None and reason is None
        outcome: tuple[str | None, str | None] | None = None
        if unit.get("handoff") and exit_code == 0 and reason is None:
            from alfrd.agent_loop import HandoffConflictError, ResponseValidationError, publish

            try:
                unit["artifact"] = publish(self.folder.root, unit["handoff"], plan_id=self.folder.id,
                                           unit_id=unit["id"], iteration=unit["iteration"], agent=unit["agent"])
                self.event(f"handoff published: {unit['id']} → {unit['artifact']['path']}")
                outcome = ("accepted", None)
            except ResponseValidationError as exc:
                reason = f"invalid handoff: {exc}"
                outcome = ("failed_validation", str(exc))
            except HandoffConflictError as exc:
                reason = f"invalid handoff: {exc}"
                outcome = ("rejected", str(exc))
            except (OSError, ValueError) as exc:
                reason = f"invalid handoff: {exc}"
                outcome = ("failed_runtime", str(exc))
        results_all: dict[str, Any] = {}
        for key in unit.get("rows") or [unit["row"]]:
            row = self.table().row(key)
            target = row.target if row else unit.get("target", "")
            code = row.code if row else unit.get("code", "")
            workdir = row.workdir if row else unit.get("workdir", "")
            need = status_from != "exit_code" or interrupted or len(unit["steps"]) > 1
            found = verify_steps(self.cfg, target, code, workdir, unit["steps"], since) if need else {}
            results_all[key] = found
            after: str | None = None   # what the remaining steps become after a non-done step
            for step in unit["steps"]:
                if after:
                    cells[(key, step)] = after
                    continue
                row_result = found.get(step)
                if reason == "cancelled":
                    state = pc.CANCELLED
                elif interrupted:
                    state = row_result["status"] if row_result else pc.INTERRUPTED
                elif reason:  # timeout
                    state = pc.FAILED
                elif status_from == "exit_code":
                    if len(unit["steps"]) > 1 and row_result:
                        state = row_result["status"]
                    else:
                        state = pc.DONE if exit_code == 0 else pc.FAILED
                elif status_from == "result_csv":
                    state = row_result["status"] if row_result else pc.FAILED
                else:
                    state = pc.FAILED if exit_code != 0 or not row_result else row_result["status"]
                cells[(key, step)] = state
                if row_result:
                    if row_result.get("workdir"):
                        fills[(key, self.cfg.workdir_column)] = row_result["workdir"]
                    if row_result.get("project_code"):
                        fills[(key, self.cfg.code_column)] = row_result["project_code"]
                if state == pc.FAILED:
                    failed_step = failed_step or step
                    after = pc.BLOCKED
                elif state != pc.DONE:
                    after = pc.TODO  # interrupted / cancelled: the rest stays to do
        self.set_cells(cells, only_if={k: {pc.TODO, pc.RUNNING} for k in cells}, fills=fills)
        states = set(cells.values()) - {pc.TODO}
        if reason == "cancelled":
            status = "cancelled"
        elif pc.FAILED in states or pc.BLOCKED in states or (not cells and exit_code != 0):
            status = "failed"
        elif pc.INTERRUPTED in states:
            status = "interrupted"
        else:
            status = "done"
        error = reason or note
        if not error and status == "failed":
            first = next(iter(results_all.values()), {})
            if failed_step and status_from != "exit_code" and failed_step not in first:
                error = f"no result CSV row for {failed_step} since {unit.get('started')}"
                if exit_code not in (0, None):
                    error += f"; exit code {exit_code}"
            elif exit_code not in (0, None):
                error = f"exit code {exit_code}"
            elif failed_step and failed_step in first:
                error = first[failed_step].get("desc") or f"{failed_step} failed (result CSV)"
        unit.update(status=status, finished=now_iso(), exit_code=exit_code,
                    results={k: v for k, v in results_all.items() if v}, error=error)
        if unit.get("handoff"):
            if outcome is None:
                outcome = (("abandoned", "cancelled") if status == "cancelled" else
                           ("rejected", unit.get("outcome_reason")) if unit.get("outcome") == "rejected" else
                           ("failed_runtime", error) if status == "failed" else
                           (unit.get("outcome"), unit.get("outcome_reason")))
            unit["outcome"], unit["outcome_reason"] = outcome
            if self._fallback_eligible(unit, exit_code, reason, outcome):
                # Run the same turn again on the next fallback model instead of stopping.
                unit["fallback_next"] = int(unit.get("fallback_index", -1)) + 1
                retry = {(key, unit["steps"][0]): pc.TODO for key in unit.get("rows") or [unit["row"]]}
                self.set_cells(retry, only_if={k: {pc.FAILED} for k in retry})
                blocked = {(key, s): pc.TODO for key in unit.get("rows") or [unit["row"]] for s in unit["steps"][1:]}
                if blocked:
                    self.set_cells(blocked, only_if={k: {pc.BLOCKED} for k in blocked})
                failed_step = None
                self.event(f"{unit['id']}: failed on its model; next attempt uses a fallback model")
        if isinstance(exit_data, dict) and isinstance(exit_data.get("usage"), dict):
            unit["usage"] = exit_data["usage"]  # peak memory, CPU s, cores, I/O, wall (alfrd.runtime.usage)
        if adopted:
            unit["note"] = "finalized after a runner restart"
        self.folder.save_unit(unit)
        self.log(f"{unit['id']}: {status} (exit {exit_code})")
        if status == "failed" and failed_step:
            rows = [r for r in (self.table().row(k) for k in unit.get("rows") or [unit["row"]]) if r]
            self.after_failure(rows, failed_step)

    def _fallback_eligible(self, unit: Mapping[str, Any], exit_code: int | None, reason: str | None,
                           outcome: tuple[str | None, str | None]) -> bool:
        """A provider/runtime failure with no usable response, and a fallback model left to try.

        Never after a validation failure, a rejection, a cancel or a timeout; the
        Claude CLI handles its own ``--fallback-model``.
        """
        if outcome[0] != "failed_runtime" or unit.get("mode") != "step":
            return False
        if reason and not str(reason).startswith("invalid handoff"):
            return False
        if exit_code is None:
            return False
        try:
            step = self.cfg.step(unit["steps"][0])
        except Exception:  # noqa: BLE001
            return False
        from alfrd.agent_io import ADAPTERS

        adapter = ADAPTERS.get(step.adapter)
        if not step.fallback_models or (adapter and adapter.native_fallback):
            return False
        return int(unit.get("fallback_index", -1)) + 1 < len(step.fallback_models)

    def after_failure(self, rows: Sequence[pc.PlanRow], step: str) -> None:
        policy = self.plan.get("on_failure") or "stop_target"
        if policy == "stop_plan":
            self.stop_new = True
            self.event(f"stopping the plan after {step} failed")
            return
        if policy != "stop_target":
            return
        steps = self.plan.get("steps") or []
        if step not in steps:
            return
        later = steps[steps.index(step) + 1:]
        cells = {(r.key, s): pc.BLOCKED for r in rows for s in later if r.cell(s) == pc.TODO}
        if cells:
            self.set_cells(cells, only_if={k: {pc.TODO} for k in cells})


# ---------------------------------------------------------------------------
# Reconcile (on server start / project load / `alfrd plan status`)


def _held_for_review(root: Path, unit: Mapping[str, Any]) -> bool:
    """A finished command whose handoff the runner holds for a person (it needs a runner, not a process)."""
    if unit.get("review_gate") != "runner" or not unit.get("exit_file"):
        return False
    exit_path = root / str(unit["exit_file"])
    exit_data = _read_json(exit_path, None)
    return isinstance(exit_data, dict) and exit_data.get("exit_code") == 0 and exit_path.with_suffix(".review.json").is_file()


def reconcile(root: str | Path, *, spawn: bool = True) -> list[dict[str, Any]]:
    """Re-attach to plans whose runner died.

    * runner alive → nothing to do.
    * a command is still alive, or the plan asked to cancel → start a runner
      (it re-adopts / cancels, then continues the queue unless paused).
    * ``auto_resume: always`` and the plan was running → start a runner.
    * otherwise → finalize ended commands here and mark the plan interrupted,
      so the Studio offers Resume.
    """
    base = Path(root).resolve()
    actions = []
    for plan in list_plans(base):
        if plan.get("status") not in ACTIVE:
            continue
        folder = PlanDir(base, plan["id"])
        if folder.runner_alive() or _starting(plan):
            continue
        units = [u for u in folder.units() if u.get("status") == "running"]
        alive = [u for u in units if pid_alive(u.get("pid"), u.get("proc_start"), u.get("host")) or _held_for_review(base, u)]
        action = folder.control()
        want = bool(alive) or action == "cancel" or (plan.get("auto_resume") == "always" and plan.get("status") == "running" and action == "run")
        if plan.get("auto_resume") == "never" and not alive:
            want = action == "cancel"
        if want:
            if spawn:
                spawn_runner(folder)
                actions.append({"plan": plan["id"], "action": "runner started", "alive": [u["id"] for u in alive]})
            else:
                actions.append({"plan": plan["id"], "action": "needs runner", "alive": [u["id"] for u in alive]})
            continue
        runner = Runner(base, plan["id"])
        for unit in units:
            runner.finalize(unit, process=None, reason=None, adopted=True)
        runner.adopt()  # stale "running" cells → interrupted
        status = "paused" if plan.get("status") == "paused" or action == "pause" else "interrupted"
        runner.save_plan(status=status, counts=runner.counts())
        runner.event(f"runner not running; plan marked {status}")
        actions.append({"plan": plan["id"], "action": status})
    return actions


def plan_status(root: str | Path, plan_id: str | None = None, *, units: int = 200) -> dict[str, Any]:
    """Everything the CLI / Studio shows for one plan (default: the latest)."""
    base = Path(root).resolve()
    plans = list_plans(base)
    if plan_id is None:
        if not plans:
            return {"plan": None, "plans": []}
        plan_id = plans[0]["id"]
    folder = PlanDir(base, plan_id)
    plan = folder.load()
    all_units = folder.units()
    waiting: list[dict[str, Any]] = []
    try:
        cfg = load_execution(base, iterations=plan.get("loop", {}).get("iterations"))
        parsed = table_for(cfg, folder.csv_path(plan), plan.get("steps"))
        if plan.get("targets"):
            parsed.rows = [r for r in parsed.rows if r.target in plan["targets"]]
        table = parsed.to_dict()
        if plan.get("status") in ACTIVE:
            waiting = waiting_rows(plan, parsed, all_units, cfg) + [w for w in plan.get("waiting") or [] if w.get("kind") == "delay"]
    except Exception as exc:  # noqa: BLE001
        table = {"error": str(exc), "rows": [], "steps": plan.get("steps") or []}
    alive = folder.runner_alive()
    runner = dict(plan.get("runner") or {})
    beat = _parse_stamp(runner.get("heartbeat"))
    runner.update(alive=alive, heartbeat_age=(datetime.now() - beat).total_seconds() if beat else None)
    durations = step_durations(base)
    from alfrd.agent_loop import status as loop_status
    from alfrd.agent_io import agent_totals

    return {
        "loop": loop_status(plan, all_units),
        "agent_totals": agent_totals(all_units),
        "plan": plan,
        "control": folder.control(),
        "runner": runner,
        "table": table,
        "units": all_units[-units:],
        "running": [u for u in all_units if u.get("status") == "running"],
        "queue": queue(table, durations, int(plan.get("concurrency") or 1), plan.get("on_failure") or "stop_target",
                       [u for u in all_units if u.get("status") == "running"]),
        "durations": durations,
        "waiting": waiting,
        "plans": [{"id": p["id"], "status": p.get("status"), "created": p.get("created"), "csv": p.get("csv"), "target": p.get("target"), "targets": p.get("targets")} for p in plans[:20]],
    }


def waiting_rows(plan: Mapping[str, Any], table: pc.PlanTable, units: Sequence[Mapping[str, Any]],
                 cfg: ExecutionConfig) -> list[dict[str, Any]]:
    """Why rows wait (read-only; the same rule the runner applies), from the plan's units."""
    if plan.get("mode") == "batch":
        return []
    limit = int(plan.get("concurrency") or 1)
    running = [u for u in units if u.get("status") == "running"]
    names, match = serialize_settings(plan, cfg)
    locks = {f"{u['code']}/{u.get('workdir') or ''}" for u in running if u.get("code")}
    picked = pick_rows(table, mode=str(plan.get("mode") or "step"), on_failure=str(plan.get("on_failure") or "stop_target"),
                       limit=limit, free=max(0, limit - len(running)), names=names, match=match, base=cfg.cwd,
                       running_rows={k for u in running for k in (u.get("rows") or [u.get("row")])},
                       started_rows={k for u in units for k in (u.get("rows") or [u.get("row")]) if k}, locks=locks)
    return picked.waiting


def queue(table: Mapping[str, Any], durations: Mapping[str, float], concurrency: int, on_failure: str,
          running: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Remaining ``todo`` cells in the order the runner takes them, with ETA offsets (s)."""
    steps = table.get("steps") or []
    now = datetime.now()
    slots: list[float] = []
    for unit in running:
        start = _parse_stamp(unit.get("started"))
        est = sum(durations.get(s, 0.0) for s in unit.get("steps") or [])
        elapsed = (now - start).total_seconds() if start else 0.0
        slots.append(max(0.0, est - elapsed))
    while len(slots) < max(1, concurrency):
        slots.append(0.0)
    out = []
    running_rows = {k for u in running for k in (u.get("rows") or [u.get("row")])}
    chains = []
    for row in table.get("rows") or []:
        cells = row.get("cells") or {}
        chain = []
        for step in steps:
            value = cells.get(step)
            if value in pc.STOP_VALUES and on_failure != "continue":
                break
            if value == pc.TODO:
                chain.append(step)
        if chain:
            chains.append((row, chain))
    # Rows already running continue first on their own slot.
    chains.sort(key=lambda item: item[0]["key"] not in running_rows)
    for row, chain in chains:
        slot = min(range(len(slots)), key=lambda i: slots[i])
        for step in chain:
            est = durations.get(step)
            out.append({"row": row["key"], "target": row["target"], "code": row.get("code"), "step": step,
                        "eta_start": slots[slot], "estimate": est})
            slots[slot] += est or 0.0
    return out


__all__ = [
    "PlanDir", "Runner", "active_plan", "add_row", "conflict_groups", "control", "create_plan", "list_plans",
    "pick_rows", "plan_status", "preview", "reconcile", "reset_failed", "resolve_csv", "spawn_runner", "step_durations", "verify_steps",
]


def manifest_hash(root, *, legacy=False):
    from alfrd.manifest_default import manifest_data

    _, path, _ = manifest_data(root)
    if path is None:
        raise ExecutionError("project manifest is missing")
    if legacy:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    import yaml
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data.pop("history", None)  # File tracking changes do not change the running workflow.
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()
