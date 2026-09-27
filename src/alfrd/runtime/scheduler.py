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


def active_plan(root: str | Path, csv_file: str | Path | None = None) -> dict[str, Any] | None:
    for plan in list_plans(root):
        if plan.get("status") in ACTIVE + ("interrupted",):
            if csv_file is None or Path(plan["csv"]).name == Path(csv_file).name:
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


def values_for(cfg: ExecutionConfig, unit: UnitSpec, plan: Mapping[str, Any], csv_file: Path) -> dict[str, Any]:
    base: dict[str, Any] = {
        "root": str(cfg.root),
        "plan_csv": str(csv_file),
        "plan_id": plan.get("id", ""),
        "steps": ",".join(unit.steps),
        "from_step": unit.steps[0] if unit.steps else "",
        "step": unit.steps[0] if len(unit.steps) == 1 else "",
    }
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
            on_failure: str | None = None, cfg: ExecutionConfig | None = None) -> dict[str, Any]:
    """Commands the runner would start, in order, if every command succeeded."""
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
    return {
        "csv": str(path),
        "mode": mode,
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


def create_plan(root: str | Path, csv_file: str | Path | None = None, *, mode: str | None = None,
                concurrency: int | None = None, on_failure: str | None = None,
                status_from: str | None = None, retry_failed: bool = False) -> PlanDir:
    cfg = load_execution(root)
    path = Path(csv_file) if csv_file else cfg.plan_csv
    if not path.is_absolute():
        path = (Path.cwd() / path) if not (cfg.root / path).exists() and (Path.cwd() / path).exists() else cfg.root / path
    if not path.exists():
        raise ExecutionError(f"plan CSV {path} does not exist (create one with `alfrd plan new`)")
    busy = active_plan(cfg.root, path)
    if busy and PlanDir(cfg.root, busy["id"]).runner_alive():
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
    if not table.steps:
        raise ExecutionError(f"{path.name} has no step columns ({', '.join(cfg.step_ids)})")
    if retry_failed:
        reset_failed(cfg, path)
    plan_id = datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{os.getpid() % 10000:04d}"
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
        "steps": table.steps,
        "mode": settings["mode"],
        "concurrency": settings["concurrency"],
        "on_failure": settings["on_failure"],
        "status_from": settings["status_from"],
        "auto_resume": settings["auto_resume"],
        "launcher": settings["launcher"],
        "kill_grace": settings.get("kill_grace", 30),
        "heartbeat": settings.get("heartbeat", 10),
        "status": "running",
        "runner": {},
        "history": [{"at": now_iso(), "event": "created"}],
    })
    folder.set_control("run")
    return folder


def reset_failed(cfg: ExecutionConfig, path: Path) -> int:
    """failed / blocked / interrupted / cancelled cells → todo."""
    table = table_for(cfg, path)
    cells = {(r.key, s): pc.TODO for r in table.rows for s in table.steps if r.cell(s) in pc.STOP_VALUES}
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
        self.stop_new = False
        self.host = socket.gethostname()
        self._cfg: ExecutionConfig | None = None
        self._cfg_key: tuple | None = None

    # -- helpers ---------------------------------------------------------
    def log(self, text: str) -> None:
        print(f"{now_iso()} {text}", flush=True)

    @property
    def cfg(self) -> ExecutionConfig:
        from alfrd.manifest_default import local_manifest

        path = local_manifest(self.folder.root)
        key = (str(path), path.stat().st_mtime_ns if path and path.exists() else None)
        if self._cfg is None or key != self._cfg_key:
            self._cfg, self._cfg_key = load_execution(self.folder.root), key
        return self._cfg

    @property
    def csv_file(self) -> Path:
        return self.folder.csv_path(self.plan)

    def table(self) -> pc.PlanTable:
        return table_for(self.cfg, self.csv_file, self.plan.get("steps"))

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
                if not self.live and not started:
                    final = "finished"
                    break
            time.sleep(POLL)
        counts = self.counts()
        self.save_plan(status=final, counts=counts, runner={"stopped": now_iso(), "heartbeat": now_iso()})
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
        busy = self._busy_rows()
        locks = {self._lock_key(l.unit) for l in self.live.values()} - {None}
        started = 0
        for row in table.rows:
            if len(self.live) >= limit:
                break
            if row.key in busy:
                continue
            spec = unit_for_row(row, table.steps, mode, self.plan["on_failure"])
            if spec is None:
                continue
            key = f"{row.code}/{row.workdir}" if row.code and limit > 1 else None
            if key and key in locks:
                continue
            started += self.launch(spec, table)
            if key:
                locks.add(key)
        return started

    @staticmethod
    def _lock_key(unit: Mapping[str, Any]) -> str | None:
        return f"{unit['code']}/{unit.get('workdir') or ''}" if unit.get("code") else None

    def _new_unit_id(self, spec: UnitSpec) -> str:
        n = len(list(self.folder.units_dir.glob("*.json"))) + 1 if self.folder.units_dir.is_dir() else 1
        label = spec.steps[0] if spec.mode == "step" else spec.mode
        return f"{n:04d}-{_slug(spec.row_key)}-{_slug(label)}"

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
        }
        cells_rows = spec.rows
        try:
            argv, env, timeout = command_for(self.cfg, spec, self.plan, self.csv_file)
        except ExecutionError as exc:
            unit.update(status="failed", error=str(exc), finished=now_iso())
            self.folder.save_unit(unit)
            self.log(f"{unit['id']}: cannot start: {exc}")
            first = spec.steps[0]
            self.set_cells({(r.key, first): pc.FAILED for r in cells_rows}, only_if={(r.key, first): {pc.TODO} for r in cells_rows})
            self.after_failure(spec.rows, first)
            return 0
        log_path = self.folder.logs_dir / f"{unit['id']}.log"
        exit_path = self.folder.logs_dir / f"{unit['id']}.exit"
        self.folder.logs_dir.mkdir(parents=True, exist_ok=True)
        cwd = self.cfg.cwd
        unit.update(argv=argv, cwd=str(cwd), log=str(log_path.relative_to(self.folder.root)),
                    exit_file=str(exit_path.relative_to(self.folder.root)), timeout=timeout)
        process_env = os.environ.copy()
        process_env.update({str(k): str(v) for k, v in (self.cfg.settings.get("env") or {}).items()})
        process_env.update(env)
        process_env.update({"ALFRD_PLAN_ID": self.folder.id, "ALFRD_TARGET": spec.target,
                            "ALFRD_STEPS": ",".join(spec.steps), "ALFRD_ROOT": str(self.folder.root)})
        wanted = {**(self.cfg.settings.get("env") or {}), **env}
        if "PYTHONPATH" in wanted:  # alfrd.yaml sets the command's PYTHONPATH itself
            process_env[ORIG_PYTHONPATH] = str(wanted["PYTHONPATH"])
        process_env = _alfrd_env(process_env)
        with open(log_path, "a", encoding="utf-8") as log:
            log.write(f"# alfrd plan {self.folder.id} · {spec.target or 'batch'} · {', '.join(spec.steps)} · {now_iso()}\n")
            log.write(f"# cwd {cwd}\n$ {' '.join(argv)}\n")
            log.flush()
            try:
                process = subprocess.Popen(
                    [sys.executable, "-m", "alfrd.runtime.shim", "--exit-file", str(exit_path), "--", *argv],
                    cwd=str(cwd), env=process_env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            except OSError as exc:
                unit.update(status="failed", error=str(exc), finished=now_iso())
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

    # -- watching --------------------------------------------------------
    def _alive(self, live: Live) -> bool:
        if live.process is not None:
            return live.process.poll() is None
        unit = live.unit
        if pid_alive(unit.get("pid"), unit.get("proc_start"), unit.get("host")):
            return True
        child = _read_json(self.folder.root / (unit.get("exit_file", "") + "").replace(".exit", ".child"), {}) or {}
        return pid_alive(child.get("pid"), child.get("proc_start"), unit.get("host"))

    def poll(self) -> None:
        for unit_id, live in list(self.live.items()):
            alive = self._alive(live)
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
        """Take over units an earlier runner left behind."""
        table = self.table()
        running_rows = set()
        for unit in self.folder.units():
            if unit.get("status") != "running":
                continue
            if unit.get("host") and unit["host"] != self.host:
                self.log(f"{unit['id']}: runs on {unit['host']}; not adopted")
                running_rows.update(unit.get("rows") or [])
                continue
            live = Live(unit=unit)
            if self._alive(live):
                self.live[unit["id"]] = live
                running_rows.update(unit.get("rows") or [unit.get("row")])
                self.log(f"{unit['id']}: re-adopted pid {unit.get('pid')}")
                self.event(f"re-adopted {unit['id']}")
            else:
                self.finalize(unit, process=None, reason=None, adopted=True)
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
        if adopted:
            unit["note"] = "finalized after a runner restart"
        self.folder.save_unit(unit)
        self.log(f"{unit['id']}: {status} (exit {exit_code})")
        if status == "failed" and failed_step:
            rows = [r for r in (self.table().row(k) for k in unit.get("rows") or [unit["row"]]) if r]
            self.after_failure(rows, failed_step)

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
        alive = [u for u in units if pid_alive(u.get("pid"), u.get("proc_start"), u.get("host"))]
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
    try:
        cfg = load_execution(base)
        table = table_for(cfg, folder.csv_path(plan), plan.get("steps")).to_dict()
    except Exception as exc:  # noqa: BLE001
        table = {"error": str(exc), "rows": [], "steps": plan.get("steps") or []}
    all_units = folder.units()
    alive = folder.runner_alive()
    runner = dict(plan.get("runner") or {})
    beat = _parse_stamp(runner.get("heartbeat"))
    runner.update(alive=alive, heartbeat_age=(datetime.now() - beat).total_seconds() if beat else None)
    durations = step_durations(base)
    return {
        "plan": plan,
        "control": folder.control(),
        "runner": runner,
        "table": table,
        "units": all_units[-units:],
        "running": [u for u in all_units if u.get("status") == "running"],
        "queue": queue(table, durations, int(plan.get("concurrency") or 1), plan.get("on_failure") or "stop_target",
                       [u for u in all_units if u.get("status") == "running"]),
        "durations": durations,
        "plans": [{"id": p["id"], "status": p.get("status"), "created": p.get("created"), "csv": p.get("csv")} for p in plans[:20]],
    }


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
    "PlanDir", "Runner", "active_plan", "control", "create_plan", "list_plans", "plan_status", "preview",
    "reconcile", "reset_failed", "spawn_runner", "step_durations", "verify_steps",
]
