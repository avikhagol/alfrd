"""Studio endpoints for running plans (alfrd.runtime.scheduler).

Reads are plain GETs. Starting, pausing, resuming, cancelling and editing a
cell are POSTs behind the app-wide loopback + CSRF gate
(``security.protect_mutation``). No request executes a command itself: a
runner is spawned as a detached process and the request returns.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from flask import jsonify, request

from alfrd.execution import ExecutionError, load_execution
from alfrd.runtime import plan_csv as pc
from alfrd.runtime import scheduler

from .studio import _json_error, _poke, _project_root, studio_api

_CSV_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ +-]*\.csv$")


def _cfg(project_name: str, target: str | None = None):
    root = _project_root(project_name)
    from alfrd.execution import load_task_execution

    return load_task_execution(root, target)


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
        cfg = _cfg(project_name, request.args.get("target"))
    except (ExecutionError, ValueError, OSError) as error:
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
    if any(step.iterations for step in cfg.steps):
        steps = cfg.step_ids
    if rows is None:
        return
    if not isinstance(rows, list) or not rows:
        raise ExecutionError("rows must be a non-empty list of {target, files, code}")
    unknown = [s for s in steps if s not in cfg.step_ids]
    if unknown:
        raise ExecutionError(f"unknown step(s): {', '.join(unknown)}")
    clean = [{"target": str(r.get("target") or "").strip(), "files": str(r.get("files") or ""),
              "code": str(r.get("code") or ""), "workdir": str(r.get("workdir") or "")}
             for r in rows if isinstance(r, dict) and str(r.get("target") or "").strip()]
    if path.exists() and (not payload.get("overwrite") or
                          (any(step.iterations for step in cfg.steps) and path.parent == cfg.root)):
        if any(step.iterations for step in cfg.steps):
            if len(clean) != 1 or payload.get("target") != clean[0]["target"]:
                raise ExecutionError("select exactly one task target when starting an agent loop")
            item = clean[0]
            target = item["target"]
            if scheduler.active_plan(cfg.root, path, target):
                raise FileExistsError(f"Task {target!r} already has an active plan")
            from alfrd.agent_loop import validate_target
            validate_target(target)
            from alfrd.execution import load_task_execution
            cfg = load_task_execution(cfg.root, target)
            with pc.locked(cfg.root / ".alfrd" / "locks" / f"{path.name}.lock"):
                table = scheduler.table_for(cfg, path)
                selected = [row for row in table.rows if row.target == target]
                if len(selected) != 1:
                    raise ExecutionError(f"unknown task target: {target}")
                selected[0].values.update({step: pc.TODO for step in cfg.step_ids})
                selected[0].values[cfg.files_column] = item["files"] or "task.md"
                pc.atomic_write(path, pc.dump(table.header, [row.values for row in table.rows]))
            return
        for row in clean:
            scheduler.add_row(cfg, path, **row, selected=steps)
        return
    pc.create(path, clean, cfg.step_ids, steps, key_column=cfg.key_column, files_column=cfg.files_column,
              code_column=cfg.code_column, workdir_column=cfg.workdir_column)


@studio_api.post("/studio/projects/<project_name>/plans/preview")
def plan_preview(project_name: str):
    """Commands the plan would run (dry run). With ``rows`` a temporary CSV is used."""
    payload = request.get_json(silent=True) or {}
    try:
        cfg = _cfg(project_name, payload.get("target"))
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
        cfg = _cfg(project_name, payload.get("target"))
        path = _csv_path(cfg, payload.get("csv"))
        with pc.locked(cfg.root / ".alfrd" / "locks" / "handoff.lock"):
            _write_rows(cfg, path, payload)
            folder = scheduler.create_plan(
                cfg.root, path, mode=payload.get("mode"), concurrency=payload.get("concurrency"),
                on_failure=payload.get("on_failure"), retry_failed=bool(payload.get("retry_failed")), target=payload.get("target"),
                start_at=payload.get("start_at") or None,
            )
        if payload.get("start", True):
            scheduler.spawn_runner(folder)
    except (FileExistsError, pc.DuplicateRowError) as error:
        return _json_error(error, 409)
    except (ExecutionError, OSError, ValueError) as error:
        return _json_error(error, 400)
    _poke(project_name)
    return jsonify(plan=folder.load()), 201


@studio_api.post("/studio/projects/<project_name>/plans/<plan_id>/<action>")
def plan_action(project_name: str, plan_id: str, action: str):
    payload = request.get_json(silent=True) or {}
    if action not in ("pause", "resume", "cancel", "start-now"):
        return _json_error(ValueError(f"unknown action {action!r}"), 404)
    if action == "cancel" and not payload.get("confirm"):
        return _json_error(ValueError("cancel stops running commands; send confirm: true"), 400)
    if not re.match(r"^[A-Za-z0-9_-]+$", plan_id):
        return _json_error(ValueError("bad plan id"), 400)
    try:
        if action == "start-now":
            plan = scheduler.start_now(_project_root(project_name), plan_id)
        else:
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
            item = {k: unit.get(k) for k in ("id", "agent", "iteration", "iterations", "status", "manual", "artifact", "error", "log", "model", "requested_model", "review_status", "human_review", "started", "finished", "row", "target", "steps", "review_gate", "outcome", "outcome_reason", "attempt_number", "retry_of", "logical_turn_id")}
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


@studio_api.post("/studio/projects/<project_name>/plans/<plan_id>/reject")
def plan_reject(project_name: str, plan_id: str):
    from alfrd.agent_loop import reject_response

    if not re.fullmatch(r"[A-Za-z0-9_-]+", plan_id):
        return _json_error(ValueError("bad plan id"), 400)
    payload = request.get_json(silent=True) or {}
    try:
        reject_response(_project_root(project_name), plan_id, str(payload.get("unit") or ""), payload.get("reason") or "")
    except FileExistsError as error:
        return _json_error(error, 409)
    except (ValueError, OSError) as error:
        return _json_error(error, 400)
    _poke(project_name)
    return jsonify(rejected=True)


@studio_api.get("/studio/projects/<project_name>/plans/<plan_id>/turns")
def plan_turns(project_name: str, plan_id: str):
    """Each step of a plan with its effective review / human-writes / delay / model, and whether it can still change."""
    try:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", plan_id):
            raise ValueError("bad plan id")
        root = _project_root(project_name)
        folder = scheduler.PlanDir(root, plan_id)
        plan = folder.load()
        runner = scheduler.Runner(root, plan_id)
        overrides = folder.overrides()
        units = folder.units()
        out = []
        for step_id in plan.get("steps") or []:
            try:
                step = runner.cfg.step(step_id)
            except Exception:  # noqa: BLE001
                continue
            attempts = [u for u in units if step_id in (u.get("steps") or [])]
            latest = attempts[-1] if attempts else {}
            state = "done" if latest.get("artifact") or latest.get("status") == "done" else latest.get("status") or "pending"
            out.append({"step": step_id, "label": step.base_step or step_id, "turn": step.turn or None, "agent": step.entrypoint,
                        "human_review": step.human_review, "manual": step.manual, "after": step.after, "at": step.at, "model": step.model,
                        "fallback_models": list(step.fallback_models), "state": state,
                        "editable": [] if state == "done" else ["human_review"] if state == "running" else sorted(scheduler.OVERRIDE_KEYS),
                        "overrides": overrides.get(step_id, {})})
        return jsonify(turns=out)
    except (ValueError, OSError, ExecutionError) as error:
        return _json_error(error, 400)


@studio_api.post("/studio/projects/<project_name>/plans/<plan_id>/turns/<step_id>")
def plan_turn_override(project_name: str, plan_id: str, step_id: str):
    """Change one step of a running plan (body: any of human_review, manual, after, model; null clears)."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", plan_id) or not re.fullmatch(r"[A-Za-z0-9+._-]+", step_id):
        return _json_error(ValueError("bad plan or step id"), 400)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _json_error(ValueError("send a JSON object of settings"), 400)
    try:
        current = scheduler.set_override(_project_root(project_name), plan_id, step_id, payload)
    except (ValueError, OSError, ExecutionError) as error:
        return _json_error(error, 400)
    _poke(project_name)
    return jsonify(overrides=current.get(step_id, {}))


@studio_api.get("/studio/projects/<project_name>/tasks")
def tasks_list(project_name: str):
    try:
        cfg = _cfg(project_name)
        folder_layout = any("{target}" in v for step in cfg.steps for v in step.handoff.values())
        plans = scheduler.list_plans(cfg.root)
        from alfrd import workspaces
        tasks = []
        for row in scheduler.table_for(cfg, cfg.plan_csv).rows:
            item = {"name": row.target, "files": row.files or "task.md"}
            if folder_layout and cfg.loop_max:
                try:
                    item["iterations"] = _cfg(project_name, row.target).steps[0].iterations
                    item["over_limit"] = item["iterations"] > cfg.loop_max
                except (ValueError, OSError, ExecutionError) as error:
                    item["error"] = str(error)
                item["workspace"] = workspaces.describe(cfg.root, row.target)
                runs = [p for p in plans if row.target in (p.get("targets") or [])]
                item["runs"] = len(runs)
                if runs:
                    item["latest"] = {k: runs[0].get(k) for k in ("id", "status", "created")}
            tasks.append(item)
        return jsonify(folder_layout=folder_layout, tasks=tasks, max_iterations=cfg.loop_max or None,
                       iteration_unit=cfg.loop_unit or None, workspace_mode=cfg.loop_workspace)
    except (ValueError, OSError) as error:
        return _json_error(error, 400)


@studio_api.post("/studio/projects/<project_name>/tasks")
def task_create(project_name: str):
    from alfrd.project_creation import task_name, task_files
    from alfrd import history
    payload = request.get_json(silent=True) or {}
    written = []
    try:
        cfg = _cfg(project_name)
        name = task_name(payload.get("name"))
        if not cfg.steps or not all(step.iterations and step.handoff and all("{target}" in v for v in step.handoff.values()) for step in cfg.steps):
            raise ValueError("This project uses legacy root handoffs; create a folder-based agent-loop project for multiple tasks")
        iterations = payload.get("iterations")
        if iterations is not None:
            cfg = load_execution(cfg.root, iterations=iterations, cap=True)
        files = task_files(cfg.root, cfg.steps, name, payload.get("task"))
        from alfrd.agent_loop import task_options
        from alfrd.execution import merged_manifest
        files[f"{name}/.alfrd-task.json"] = json.dumps(task_options(merged_manifest(cfg.root).get("_workflow") or {}, cfg.steps[0].iterations)) + "\n"
        with pc.locked(cfg.root / ".alfrd" / "locks" / "handoff.lock"):
            destination = cfg.root / name
            destination.mkdir(exist_ok=False)
            try:
                for rel, text in files.items():
                    path = cfg.root / rel
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(text, encoding="utf-8")
                    written.append(path)
                with pc.locked(cfg.root / ".alfrd" / "locks" / f"{cfg.plan_csv.name}.lock"):
                    if cfg.plan_csv.exists():
                        table = scheduler.table_for(cfg, cfg.plan_csv)
                        missing = [step for step in cfg.step_ids if step not in table.header]
                        if missing:
                            pc.atomic_write(cfg.plan_csv, pc.dump([*table.header, *missing], [row.values for row in table.rows]))
                scheduler.add_row(cfg, cfg.plan_csv, target=name, files="task.md", create=True)
            except Exception:
                for path in reversed(written):
                    path.unlink(missing_ok=True)
                destination.rmdir()
                raise
            for rel in files:
                if history.is_tracked(cfg.root, rel):
                    history.record(cfg.root, rel, source="studio")
        workspace = {}
        if cfg.loop_workspace == "worktree":
            from alfrd import workspaces
            try:
                workspace = workspaces.ensure(cfg.root, name)
            except ValueError as error:
                workspace = {"workspace": None, "warning": str(error)}
        _poke(project_name)
        return jsonify(name=name, files="task.md", **workspace), 201
    except (FileExistsError, pc.DuplicateRowError) as error:
        return _json_error(error, 409)
    except (ValueError, OSError) as error:
        return _json_error(error, 400)


@studio_api.patch("/studio/projects/<project_name>/tasks/<old>")
def task_rename(project_name: str, old: str):
    from alfrd.project_creation import task_name
    from alfrd.agent_loop import project_file
    from alfrd import history
    payload = request.get_json(silent=True) or {}
    try:
        cfg = _cfg(project_name)
        old, name = task_name(old), task_name(payload.get("name"))
        with pc.locked(cfg.root / ".alfrd" / "locks" / "handoff.lock"):
            if scheduler.active_plan(cfg.root, cfg.plan_csv, old):
                raise FileExistsError(f"Task {old!r} has an active plan; finish or cancel it before renaming")
            source, destination = project_file(cfg.root, old), project_file(cfg.root, name)
            if destination.exists() or destination.is_symlink():
                raise FileExistsError(f"Task {name!r} already exists")
            with pc.locked(cfg.root / ".alfrd" / "locks" / f"{cfg.plan_csv.name}.lock"):
                table = scheduler.table_for(cfg, cfg.plan_csv)
                rows = [r for r in table.rows if r.target == old]
                if not rows or not source.is_dir():
                    raise ValueError(f"unknown task: {old}")
                for row in table.rows:
                    if row.target == old:
                        row.values[table.key_column] = name
                    row.values[table.files_column] = ",".join(name + f[len(old):] if f.startswith(old + "/") else f
                                                            for f in pc.join_files(row.files).split(","))
                source.rename(destination)
                try:
                    pc.atomic_write(cfg.plan_csv, pc.dump(table.header, [r.values for r in table.rows]))
                except Exception:
                    destination.rename(source)
                    raise
                from alfrd import workspaces
                workspaces.repair(cfg.root, name)  # a worktree inside the moved folder
            from alfrd.studio_defs import manifest_file
            import yaml
            manifest = manifest_file(cfg.root)
            if manifest:
                data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
                patterns = (data.get("history") or {}).get("files", [])
                if isinstance(patterns, str):
                    patterns = [patterns]
                updated = [name + pattern[len(old):] if pattern.startswith(old + "/") else pattern for pattern in patterns]
                if updated != patterns:
                    data["history"]["files"] = updated
                    from alfrd.yaml_text import dump
                    pc.atomic_write(manifest, dump(data))
            # Only the moved files' archives: a glob on "old__*" would also match a task named "old__x".
            for path in destination.rglob("*"):
                sub = path.relative_to(destination).as_posix()
                archive = history._dir(cfg.root, f"{old}/{sub}")
                if path.is_file() and archive.is_dir():
                    archive.rename(history._dir(cfg.root, f"{name}/{sub}"))
            for rel in history.tracked(cfg.root):
                if rel.startswith(name + "/"):
                    history.record(cfg.root, rel, source="studio", message=f"Renamed task from {old}")
        _poke(project_name)
        return jsonify(name=name)
    except FileExistsError as error:
        return _json_error(error, 409)
    except (ValueError, OSError) as error:
        return _json_error(error, 400)


@studio_api.route("/studio/projects/<project_name>/task", methods=["GET", "POST"])
@studio_api.route("/studio/projects/<project_name>/tasks/<target>/task", methods=["GET", "POST"])
def project_task(project_name: str, target: str | None = None):
    from alfrd import history
    from alfrd.agent_loop import project_file

    root = _project_root(project_name)
    try:
        cfg = load_execution(root)
        from alfrd.project_creation import task_name
        if not cfg.steps:
            raise ValueError("This project has no task workflow")
        target = task_name(target or cfg.steps[0].loop_options.get("task_row", "task"))
        if not cfg.steps:
            raise ValueError("This project has no task workflow")
        scoped = any("{target}" in v for step in cfg.steps for v in step.handoff.values())
        rel = f"{target}/task.md" if scoped else "task.md"
        path = project_file(root, rel)
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
                if sync and scheduler.active_plan(root, cfg.plan_csv, target):
                    return _json_error(ValueError("Finish or cancel the current plan before replacing its initial handoff"), 409)
                history.save(root, rel, text, source="studio", base_hash=payload["base_hash"] if path.exists() else None)
                if sync and cfg.steps and cfg.steps[0].handoff:
                    from alfrd.agent_loop import resolve_handoff
                    incoming = resolve_handoff(root, cfg.steps[0].handoff["input"], target).relative_to(root).as_posix()
                    history.save(root, incoming, text, source="studio", message="New task for next run")
                _poke(project_name)
            text = path.read_text(encoding="utf-8") if path.exists() else ""
            return jsonify(file=rel, text=text, hash=history.text_hash(text))
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
        from alfrd.agent_loop import resolve_handoff
        targets = [r.target for r in scheduler.table_for(cfg, cfg.plan_csv).rows]
        allowed = {resolve_handoff(root, v, target).relative_to(root).as_posix()
                   for target in targets for step in cfg.steps for v in step.handoff.values()}
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
