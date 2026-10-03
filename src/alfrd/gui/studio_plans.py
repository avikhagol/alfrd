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
    data["loop"] = dict(cfg.steps[0].loop_options) if cfg.steps else {}
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


MAX_ARTIFACT_BYTES = 256 * 1024


def _handoff_folder(project_name, plan_id):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", plan_id):
        raise ValueError("bad plan id")
    folder = scheduler.PlanDir(_project_root(project_name), plan_id)
    folder.load()
    return folder


def _artifact_path(folder, unit, artifact):
    if artifact not in ("prompt", "response"):
        raise ValueError("unknown artifact")
    archive = (folder.path / "handoffs").resolve()
    if not archive.is_relative_to(folder.path.resolve()):
        raise ValueError("archive is outside the plan directory")
    path = Path(unit["handoff"][artifact + "_file"]).resolve()
    if not path.is_relative_to(archive):
        raise ValueError("artifact is outside the plan archive")
    return path


@studio_api.get("/studio/projects/<project_name>/plans/<plan_id>/handoffs")
def plan_handoffs(project_name: str, plan_id: str):
    try:
        folder = _handoff_folder(project_name, plan_id)
        iterations = folder.load().get("loop", {}).get("iterations", "?")
        out = []
        for unit in folder.units():
            if not unit.get("handoff"):
                continue
            item = {k: unit.get(k) for k in ("id", "agent", "iteration", "iterations", "status", "manual", "artifact", "error", "log", "model", "requested_model", "review_status", "human_review", "started", "finished", "row", "steps")}
            from alfrd.agent_loop import turn_phase
            item["phase"] = turn_phase(unit)
            item["iteration_label"] = f"{unit.get('iteration')}/{unit.get('iterations') or iterations}"
            for artifact in ("prompt", "response"):
                path = _artifact_path(folder, unit, artifact)
                item[artifact + "_bytes"] = path.stat().st_size if path.is_file() else 0
            out.append(item)
        return jsonify(handoffs=out)
    except (ValueError, OSError, ExecutionError) as error:
        return _json_error(error, 400)


@studio_api.get("/studio/projects/<project_name>/plans/<plan_id>/handoffs/<unit_id>/<artifact>")
def handoff_artifact(project_name: str, plan_id: str, unit_id: str, artifact: str):
    try:
        if not re.fullmatch(r"[A-Za-z0-9+._-]+", unit_id) or unit_id in (".", ".."):
            raise ValueError("invalid unit id")
        if artifact not in ("prompt", "response"):
            raise ValueError("unknown artifact")
        folder = _handoff_folder(project_name, plan_id)
        unit = next((u for u in folder.units() if u["id"] == unit_id and u.get("handoff")), None)
        if unit is None:
            return _json_error(ValueError("unknown unit"), 404)
        offset = int(request.args.get("offset", 0))
        limit = min(int(request.args.get("limit", MAX_ARTIFACT_BYTES)), MAX_ARTIFACT_BYTES)
        if offset < 0 or limit < 4:
            raise ValueError("offset must be nonnegative and limit at least 4 bytes")
        path = _artifact_path(folder, unit, artifact)
        if not path.is_file():
            return _json_error(ValueError("artifact is not available"), 404)
        total = path.stat().st_size
        from alfrd.agent_loop import MAX_RESPONSE_BYTES
        if total > MAX_RESPONSE_BYTES:
            raise ValueError("handoff archive exceeds 10 MiB")
        if offset > total:
            raise ValueError("offset exceeds artifact size")
        with path.open("rb") as stream:
            stream.seek(offset)
            data = stream.read(limit)
        # A caller resumes at offset + returned_bytes; never drop a partial character.
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError as error:
            if error.end != len(data) or error.reason != "unexpected end of data":
                raise ValueError("offset is not a UTF-8 boundary") from error
            data = data[:error.start]
            content = data.decode("utf-8")
        return jsonify(content=content, offset=offset, returned_bytes=len(data),
                       total_bytes=total, truncated=offset + len(data) < total)
    except (ValueError, OSError, ExecutionError) as error:
        return _json_error(error, 400)


@studio_api.post("/studio/projects/<project_name>/plans/<plan_id>/response")
def plan_response(project_name: str, plan_id: str):
    from alfrd.agent_loop import submit_response, ResponseValidationError

    if not re.fullmatch(r"[A-Za-z0-9_-]+", plan_id):
        return _json_error(ValueError("bad plan id"), 400)
    payload = request.get_json(silent=True) or {}
    try:
        submit_response(_project_root(project_name), plan_id, str(payload.get("unit") or ""), payload.get("text"))
    except ResponseValidationError as error:
        return jsonify(errors=error.errors), 400
    except FileExistsError as error:
        return _json_error(error, 409)
    except (ValueError, OSError) as error:
        return _json_error(error, 400)
    return jsonify(submitted=True)


@studio_api.post("/studio/projects/<project_name>/plans/<plan_id>/review")
def plan_review(project_name: str, plan_id: str):
    from alfrd.agent_loop import approve_response
    from alfrd import history

    if not re.fullmatch(r"[A-Za-z0-9_-]+", plan_id):
        return _json_error(ValueError("bad plan id"), 400)
    payload = request.get_json(silent=True) or {}
    try:
        approve_response(_project_root(project_name), plan_id, str(payload.get("unit") or ""),
                         payload.get("text"), payload.get("base_hash"))
    except (history.Conflict, FileExistsError) as error:
        return _json_error(error, 409)
    except (ValueError, OSError) as error:
        return _json_error(error, 400)
    _poke(project_name)
    return jsonify(approved=True)


@studio_api.route("/studio/projects/<project_name>/task", methods=["GET", "POST"])
def project_task(project_name: str):
    from alfrd import history
    from alfrd.agent_loop import project_file

    root = _project_root(project_name)
    path = project_file(root, "task.md")
    try:
        with pc.locked(root / ".alfrd" / "locks" / "handoff.lock"):
            if request.method == "POST":
                payload = request.get_json(silent=True) or {}
                text = payload.get("text")
                if not isinstance(text, str) or not text.strip() or len(text.encode()) > 1024 * 1024:
                    raise ValueError("task must be nonempty Markdown smaller than 1 MiB")
                if not payload.get("base_hash"):
                    raise ValueError("base_hash is required")
                current = path.read_text(encoding="utf-8") if path.exists() else ""
                if history.text_hash(current) != payload["base_hash"]:
                    raise history.Conflict("task.md", current, text)
                cfg = load_execution(root)
                sync = payload.get("use_for_next_run", False)
                if not isinstance(sync, bool):
                    raise ValueError("use_for_next_run must be true or false")
                if sync and any(p.get("status") in ("running", "paused", "interrupted") for p in scheduler.list_plans(root)):
                    return _json_error(ValueError("Finish or cancel the current plan before replacing its initial handoff"), 409)
                history.save(root, "task.md", text, source="studio", base_hash=payload["base_hash"] if path.exists() else None)
                if sync and cfg.steps and cfg.steps[0].handoff:
                    history.save(root, cfg.steps[0].handoff["input"], text, source="studio", message="New task for next run")
                _poke(project_name)
            text = path.read_text(encoding="utf-8") if path.exists() else ""
            return jsonify(file="task.md", text=text, hash=history.text_hash(text))
    except history.Conflict as error:
        return jsonify(error={"message": str(error)}, current=error.current, hash=error.current_hash), 409
    except (ValueError, OSError) as error:
        return _json_error(error, 400)


@studio_api.route("/studio/projects/<project_name>/handoff", methods=["GET", "POST"])
def edit_handoff(project_name: str):
    from alfrd import history
    from alfrd.agent_loop import project_file

    root = _project_root(project_name)
    payload = request.get_json(silent=True) or {} if request.method == "POST" else request.args
    rel = str(payload.get("file") or "")
    try:
        cfg = load_execution(root)
        allowed = {v for step in cfg.steps for v in step.handoff.values()}
        if rel not in allowed:
            raise ValueError("not a workflow handoff file")
        path = project_file(root, rel)
        if request.method == "POST":
            if not isinstance(payload.get("text"), str) or not payload.get("base_hash"):
                raise ValueError("text and base_hash are required")
            # Publication and editor saves share the handoff lock.
            with pc.locked(root / ".alfrd" / "locks" / "handoff.lock"):
                history.save(root, rel, payload["text"], source="studio", base_hash=payload["base_hash"])
        text = path.read_text(encoding="utf-8")
        return jsonify(file=rel, text=text, hash=history.text_hash(text))
    except history.Conflict as error:
        return jsonify(error={"message": str(error)}, current=error.current, hash=error.current_hash), 409
    except (ValueError, OSError) as error:
        return _json_error(error, 400)


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
    if not re.fullmatch(r"[A-Za-z0-9_-]+", plan_id):
        return _json_error(ValueError("bad plan id"), 400)
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
            if plan.get("loop") and value != pc.TODO:
                raise ExecutionError("agent loop turns cannot be skipped")
            cells[(str(item.get("row")), str(item.get("step")))] = value or "skip"
        # A running cell is left alone; everything else may be retried or skipped.
        only_if = {k: set(pc.STATES) - {pc.RUNNING} for k in cells}
        pc.update(folder.csv_path(plan), folder.csv_lock(plan), plan.get("steps") or cfg.step_ids, cells,
                  only_if=only_if, key_column=cfg.key_column, code_column=cfg.code_column,
                  workdir_column=cfg.workdir_column, files_column=cfg.files_column)
    except (ExecutionError, OSError) as error:
        return _json_error(error, 400)
    _poke(project_name)
    return jsonify(ok=True)


__all__: list[str] = []
