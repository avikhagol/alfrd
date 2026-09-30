"""Studio endpoints for running plans (alfrd.runtime.scheduler).

Reads are plain GETs. Starting, pausing, resuming, cancelling and editing a
cell are POSTs behind the app-wide loopback + CSRF gate
(``security.protect_mutation``). No request executes a command itself: a
runner is spawned as a detached process and the request returns.
"""

from __future__ import annotations

import re
from pathlib import Path

from flask import jsonify, request

from alfrd.execution import ExecutionError, load_execution
from alfrd.runtime import plan_csv as pc
from alfrd.runtime import scheduler

from .studio import _json_error, _poke, _project_root, studio_api

_CSV_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ +-]*\.csv$")


def _cfg(project_name: str):
    return load_execution(_project_root(project_name))


def _csv_path(cfg, name: str | None) -> Path:
    if not name:
        return cfg.plan_csv
    if not _CSV_NAME.match(name):
        raise ExecutionError("plan CSV must be a file name like alfrd.plan.csv (in the project folder)")
    return cfg.root / name


@studio_api.get("/studio/projects/<project_name>/execution")
def execution_settings(project_name: str):
    """How steps run (alfrd.yaml `entrypoint`/`execution`), plus the plan CSV if it exists."""
    try:
        cfg = _cfg(project_name)
    except ExecutionError as error:
        return jsonify(error={"code": 400, "message": str(error)}, configured=False), 200
    data = cfg.to_dict()
    data["configured"] = any(s.argv for s in cfg.steps)
    data["plan_csv_exists"] = cfg.plan_csv.exists()
    data["plan_csv_name"] = cfg.plan_csv.name
    if cfg.plan_csv.exists():
        data["table"] = scheduler.table_for(cfg, cfg.plan_csv).to_dict()
    return jsonify(data)


def _write_rows(cfg, path: Path, payload: dict) -> None:
    rows = payload.get("rows")
    steps = payload.get("steps") or []
    if rows is None:
        return
    if not isinstance(rows, list) or not rows:
        raise ExecutionError("rows must be a non-empty list of {target, files, code}")
    unknown = [s for s in steps if s not in cfg.step_ids]
    if unknown:
        raise ExecutionError(f"unknown step(s): {', '.join(unknown)}")
    if path.exists() and not payload.get("overwrite"):
        raise FileExistsError(f"{path.name} exists; confirm to replace it")
    clean = [{"target": str(r.get("target") or "").strip(), "files": str(r.get("files") or ""),
              "code": str(r.get("code") or ""), "workdir": str(r.get("workdir") or "")}
             for r in rows if isinstance(r, dict) and str(r.get("target") or "").strip()]
    pc.create(path, clean, cfg.step_ids, steps, key_column=cfg.key_column, files_column=cfg.files_column,
              code_column=cfg.code_column, workdir_column=cfg.workdir_column)


@studio_api.post("/studio/projects/<project_name>/plans/preview")
def plan_preview(project_name: str):
    """Commands the plan would run (dry run). With ``rows`` a temporary CSV is used."""
    payload = request.get_json(silent=True) or {}
    try:
        cfg = _cfg(project_name)
        path = _csv_path(cfg, payload.get("csv"))
        if payload.get("rows") is not None:
            path = cfg.root / ".alfrd" / "tmp" / "preview.plan.csv"
            path.parent.mkdir(parents=True, exist_ok=True)
            _write_rows(cfg, path, {**payload, "overwrite": True})
        result = scheduler.preview(cfg.root, path, mode=payload.get("mode"), on_failure=payload.get("on_failure"), cfg=cfg,
                                   concurrency=payload.get("concurrency"))
    except (ExecutionError, OSError) as error:
        return _json_error(error, 400)
    return jsonify(result)


@studio_api.get("/studio/projects/<project_name>/plans")
def plans_list(project_name: str):
    """Latest plan (or ``?id=``) with grid, units, queue, runner; plus the plan list."""
    root = _project_root(project_name)
    notes = scheduler.reconcile(root, spawn=False)
    try:
        data = scheduler.plan_status(root, request.args.get("id") or None)
    except ExecutionError as error:
        return _json_error(error, 404)
    data["reconcile"] = notes
    return jsonify(data)


@studio_api.post("/studio/projects/<project_name>/plans/reconcile")
def plans_reconcile(project_name: str):
    """Start a runner for plans whose commands still run but whose runner stopped."""
    root = _project_root(project_name)
    return jsonify(actions=scheduler.reconcile(root, spawn=True))


@studio_api.post("/studio/projects/<project_name>/plans")
def plan_start(project_name: str):
    """Write the plan CSV (when ``rows`` are given), create a plan and spawn its runner."""
    payload = request.get_json(silent=True) or {}
    try:
        cfg = _cfg(project_name)
        path = _csv_path(cfg, payload.get("csv"))
        _write_rows(cfg, path, payload)
        folder = scheduler.create_plan(
            cfg.root, path, mode=payload.get("mode"), concurrency=payload.get("concurrency"),
            on_failure=payload.get("on_failure"), retry_failed=bool(payload.get("retry_failed")),
        )
        if payload.get("start", True):
            scheduler.spawn_runner(folder)
    except FileExistsError as error:
        return _json_error(error, 409)
    except (ExecutionError, OSError, ValueError) as error:
        return _json_error(error, 400)
    _poke(project_name)
    return jsonify(plan=folder.load()), 201


@studio_api.post("/studio/projects/<project_name>/plans/<plan_id>/<action>")
def plan_action(project_name: str, plan_id: str, action: str):
    payload = request.get_json(silent=True) or {}
    if action not in ("pause", "resume", "cancel"):
        return _json_error(ValueError(f"unknown action {action!r}"), 404)
    if action == "cancel" and not payload.get("confirm"):
        return _json_error(ValueError("cancel stops running commands; send confirm: true"), 400)
    if not re.match(r"^[A-Za-z0-9_-]+$", plan_id):
        return _json_error(ValueError("bad plan id"), 400)
    try:
        plan = scheduler.control(_project_root(project_name), plan_id, action,
                                 retry_failed=bool(payload.get("retry_failed")))
    except ExecutionError as error:
        return _json_error(error, 404)
    _poke(project_name)
    return jsonify(plan=plan)


def _row_steps(payload: dict, plan_steps: list[str]) -> list[str] | None:
    """``steps`` from an add-row request: ``None`` (every step column), or a non-empty subset of the plan's steps."""
    steps = payload.get("steps")
    if steps is None:
        return None
    if not isinstance(steps, list):
        raise ExecutionError("steps must be a list of step ids")
    if not steps:
        raise ExecutionError("select at least one step")
    unknown = [str(s) for s in steps if s not in plan_steps]
    if unknown:
        raise ExecutionError(f"unknown step(s): {', '.join(unknown)}")
    return [str(s) for s in steps]


@studio_api.post("/studio/projects/<project_name>/plans/<plan_id>/rows")
def plan_add_row(project_name: str, plan_id: str):
    """Append one row (target, files, project code) to this plan's CSV; never creates the CSV.

    A live runner picks the row up on its next pass over the CSV; a paused,
    finished or stopped plan runs it after Resume / Run remaining. ``steps``
    (a non-empty subset of the plan's step ids) marks which cells start
    ``todo``; default is every step column the CSV already has. ``files`` may
    be a string or a list (comma, newline or space separated).
    """
    payload = request.get_json(silent=True) or {}
    if not re.match(r"^[A-Za-z0-9_-]+$", plan_id):
        return _json_error(ValueError("bad plan id"), 400)
    root = _project_root(project_name)
    folder = scheduler.PlanDir(root, plan_id)
    try:
        plan = folder.load()
    except ExecutionError as error:
        return _json_error(error, 404)
    try:
        cfg = load_execution(root)
        plan_steps = list(plan.get("steps") or cfg.step_ids)
        table = scheduler.add_row(
            cfg, folder.csv_path(plan), target=str(payload.get("target") or ""),
            files=pc.join_files(payload.get("files")), code=str(payload.get("code") or ""),
            workdir=str(payload.get("workdir") or ""), selected=_row_steps(payload, plan_steps), steps=plan_steps,
        )
    except pc.DuplicateRowError as error:
        return _json_error(error, 409)
    except FileNotFoundError as error:
        return _json_error(error, 400)
    except (ExecutionError, ValueError) as error:
        return _json_error(error, 400)
    except OSError as error:
        return _json_error(error, 500)
    _poke(project_name)
    return jsonify(table=table.to_dict()), 201


@studio_api.post("/studio/projects/<project_name>/plans/<plan_id>/cells")
def plan_cells(project_name: str, plan_id: str):
    """Set cells: ``{cells: [{row, step, value}]}`` with value todo | skip (retry = todo)."""
    payload = request.get_json(silent=True) or {}
    items = payload.get("cells") or []
    try:
        root = _project_root(project_name)
        folder = scheduler.PlanDir(root, plan_id)
        plan = folder.load()
        cfg = load_execution(root)
        cells = {}
        for item in items:
            value = str(item.get("value") or "")
            if value not in (pc.TODO, "skip", ""):
                raise ExecutionError("a cell can be set to todo or skip")
            cells[(str(item.get("row")), str(item.get("step")))] = value or "skip"
        # A running cell is left alone; everything else may be retried or skipped.
        only_if = {k: set(pc.STATES) - {pc.RUNNING} for k in cells}
        pc.update(folder.csv_path(plan), folder.csv_lock(plan), plan.get("steps") or cfg.step_ids, cells,
                  only_if=only_if, key_column=cfg.key_column, code_column=cfg.code_column,
                  workdir_column=cfg.workdir_column, files_column=cfg.files_column)
    except ExecutionError as error:
        return _json_error(error, 400)
    _poke(project_name)
    return jsonify(ok=True)


__all__: list[str] = []
