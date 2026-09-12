from __future__ import annotations

import csv
import io
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from flask import Blueprint, Response, abort, current_app, jsonify, render_template, request

from alfrd import __version__
from alfrd.runtime import RuntimeNotFound
from alfrd.runtime.matrix import (
    DETAIL_FIELDS,
    MATRIX_STATUSES,
    MatrixQueryService,
    cell_detail,
)


api = Blueprint("api", __name__, url_prefix="/api")
dashboard = Blueprint("dashboard", __name__, url_prefix="/dashboard")
system = Blueprint("system", __name__)
control = Blueprint("control", __name__, url_prefix="/api/runtime")


def _reader():
    return current_app.extensions["alfrd_catalog_reader"]


def _runtime_service():
    """The optional ``RuntimeService`` backing the sheet-like matrix.

    Returns ``None`` when the application was not configured with runtime
    persistence (e.g. the plain catalog-only deployment), so matrix routes
    can return a stable 404 instead of raising.
    """

    return current_app.config.get("RUNTIME_SERVICE")


def _control_runtime_service():
    """Return the configured ``RuntimeService`` for control routes.

    Control routes are only registered/usable when the application was
    configured with a ``RUNTIME_SERVICE``; without it they return 503 rather
    than silently no-op, so a read-only catalog deployment stays inert.
    """
    service = current_app.config.get("RUNTIME_SERVICE")
    if service is None:
        abort(503, description="Runtime execution control is not configured for this app")
    return service


def _project_or_404(project_name: str):
    # Deliberately resolve metadata before any child record.  The reader is a
    # catalog adapter; this path never instantiates or imports a Project.
    project = _reader().get_project(project_name)
    if project is None:
        abort(404, description=f"Project {project_name!r} not found")
    return project


def _item_or_404(project_name: str, item_name: str, getter_name: str):
    project = _project_or_404(project_name)
    item = getattr(_reader(), getter_name)(project["id"], item_name)
    if item is None:
        abort(404, description=f"{item_name!r} not found in project {project_name!r}")
    return item


def _workflow_id_or_404(project_name: str, workflow_name: str) -> tuple[str, str]:
    project = _project_or_404(project_name)
    workflow = _reader().get_workflow(project["id"], workflow_name)
    if workflow is None:
        abort(404, description=f"workflow {workflow_name!r} not found in project {project_name!r}")
    return project["id"], workflow["id"]


def _matrix_or_404(project_name: str, workflow_name: str):
    service = _runtime_service()
    if service is None:
        abort(404, description="the sheet-like matrix requires a configured runtime service")
    project_id, workflow_id = _workflow_id_or_404(project_name, workflow_name)
    status_filter = request.args.get("status") or None
    search = request.args.get("search") or None
    try:
        return service, MatrixQueryService(service).build(
            project_id, workflow_id, status_filter=status_filter, search=search
        )
    except RuntimeNotFound as error:
        abort(404, description=str(error))


@system.get("/health")
@api.get("/health")
def health():
    return {"status": "ok"}


@system.get("/version")
@api.get("/version")
def version():
    return {"version": __version__}


@api.get("/")
def api_index():
    return {"name": "alfrd", "version": __version__}


@api.get("/projects")
def projects_api():
    return {"projects": _reader().list_projects()}


@api.get("/projects/<project_name>")
@api.get("/project/<project_name>")
def project_api(project_name: str):
    return _project_or_404(project_name)


@api.get("/projects/<project_name>/manifest")
def manifest_api(project_name: str):
    project = _project_or_404(project_name)
    return _reader().get_manifest(project["id"])


@api.get("/projects/<project_name>/workflows")
def workflows_api(project_name: str):
    project = _project_or_404(project_name)
    return {"workflows": _reader().list_workflows(project["id"])}


@api.get("/projects/<project_name>/workflows/<item_name>")
def workflow_api(project_name: str, item_name: str):
    return _item_or_404(project_name, item_name, "get_workflow")


@api.get("/projects/<project_name>/steps")
def steps_api(project_name: str):
    project = _project_or_404(project_name)
    return {"steps": _reader().list_steps(project["id"])}


@api.get("/projects/<project_name>/steps/<item_name>")
def step_api(project_name: str, item_name: str):
    return _item_or_404(project_name, item_name, "get_step")


@api.get("/projects/<project_name>/validators")
def validators_api(project_name: str):
    project = _project_or_404(project_name)
    return {"validators": _reader().list_validators(project["id"])}


@api.get("/projects/<project_name>/validators/<item_name>")
def validator_api(project_name: str, item_name: str):
    return _item_or_404(project_name, item_name, "get_validator")


@api.get("/projects/<project_name>/parameters")
def parameters_api(project_name: str):
    project = _project_or_404(project_name)
    return {"parameters": _reader().list_parameters(project["id"])}


@api.get("/projects/<project_name>/parameters/<item_name>")
def parameter_api(project_name: str, item_name: str):
    return _item_or_404(project_name, item_name, "get_parameter")


@api.get("/projects/<project_name>/dataset-columns")
def dataset_columns_api(project_name: str):
    project = _project_or_404(project_name)
    return {"dataset_columns": _reader().list_dataset_columns(project["id"])}


@api.get("/projects/<project_name>/dataset-columns/<item_name>")
def dataset_column_api(project_name: str, item_name: str):
    return _item_or_404(project_name, item_name, "get_dataset_column")


@api.get("/projects/<project_name>/artifact-definitions")
def artifact_definitions_api(project_name: str):
    project = _project_or_404(project_name)
    return {
        "artifact_definitions": _reader().list_artifact_definitions(project["id"])
    }


@api.get("/projects/<project_name>/artifact-definitions/<item_name>")
def artifact_definition_api(project_name: str, item_name: str):
    return _item_or_404(project_name, item_name, "get_artifact_definition")


@dashboard.get("/")
def index_dashboard():
    return render_template(
        "dashboard/index.htm", title="Projects", projects=_reader().list_projects()
    )


@dashboard.get("/project/<project_name>")
def project_details(project_name: str):
    project = _project_or_404(project_name)
    project_id = project["id"]
    return render_template(
        "dashboard/project_details.htm",
        title=project["name"],
        project=project,
        manifest=_reader().get_manifest(project_id),
        workflows=_reader().list_workflows(project_id),
        steps=_reader().list_steps(project_id),
        validators=_reader().list_validators(project_id),
        parameters=_reader().list_parameters(project_id),
        dataset_columns=_reader().list_dataset_columns(project_id),
        artifact_definitions=_reader().list_artifact_definitions(project_id),
    )


@dashboard.get("/project/<project_name>/workflows/<workflow_name>/matrix")
def matrix_dashboard(project_name: str, workflow_name: str):
    project = _project_or_404(project_name)
    _service, matrix = _matrix_or_404(project_name, workflow_name)
    return render_template(
        "dashboard/matrix.htm",
        title=f"{project['name']} / {workflow_name}",
        project=project,
        workflow_name=workflow_name,
        matrix=matrix,
        status_filter=request.args.get("status") or "",
        search=request.args.get("search") or "",
        statuses=MATRIX_STATUSES,
    )


@api.get("/projects/<project_name>/workflows/<workflow_name>/matrix")
def matrix_api(project_name: str, workflow_name: str):
    _service, matrix = _matrix_or_404(project_name, workflow_name)
    return jsonify(matrix.to_dict())


@api.get("/projects/<project_name>/workflows/<workflow_name>/matrix.csv")
def matrix_csv_api(project_name: str, workflow_name: str):
    _service, matrix = _matrix_or_404(project_name, workflow_name)
    buffer = io.StringIO()
    fieldnames = ["dataset_id", "dataset_name", "run_status", *matrix.steps]
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    for row in matrix.rows:
        record = {
            "dataset_id": row.dataset_external_id,
            "dataset_name": row.dataset_name or "",
            "run_status": row.run_status,
        }
        record.update({step: row.cells[step].status for step in matrix.steps})
        writer.writerow(record)
    return Response(buffer.getvalue(), mimetype="text/csv")


@api.get("/projects/<project_name>/workflows/<workflow_name>/matrix-details.csv")
def matrix_details_csv_api(project_name: str, workflow_name: str):
    _service, matrix = _matrix_or_404(project_name, workflow_name)
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=DETAIL_FIELDS)
    writer.writeheader()
    for row in matrix.rows:
        for step in matrix.steps:
            cell = row.cells[step]
            writer.writerow({
                "dataset_id": row.dataset_external_id,
                "step": step,
                "status": cell.status,
                "attempt": cell.attempt,
                "started_at": cell.started_at or "",
                "finished_at": cell.finished_at or "",
                "duration_seconds": cell.duration_seconds if cell.duration_seconds is not None else "",
                "exit_code": cell.exit_code if cell.exit_code is not None else "",
                "result_summary": cell.result_summary or "",
                "error_summary": cell.error_summary or "",
                "artifact_count": cell.artifact_count,
            })
    return Response(buffer.getvalue(), mimetype="text/csv")


@api.get("/projects/<project_name>/executions/<execution_id>")
def execution_detail_api(project_name: str, execution_id: str):
    _project_or_404(project_name)
    service = _runtime_service()
    if service is None:
        abort(404, description="the sheet-like matrix requires a configured runtime service")
    try:
        return jsonify(cell_detail(service, execution_id))
    except RuntimeNotFound as error:
        abort(404, description=str(error))


@api.post("/projects/<project_name>/datasets/import")
def datasets_import_api(project_name: str):
    """Import datasets from an uploaded CSV file.

    This is the only mutating route in the matrix lane: it registers dataset
    identity rows so they appear in the matrix, and does not start, resume,
    retry, or cancel any run.
    """

    project = _project_or_404(project_name)
    service = _runtime_service()
    if service is None:
        abort(404, description="dataset import requires a configured runtime service")
    upload = request.files.get("file")
    if upload is None:
        abort(400, description="a 'file' upload field is required")
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as handle:
        handle.write(upload.read())
        temp_path = Path(handle.name)
    try:
        imported = service.import_datasets_csv(project["id"], temp_path)
    finally:
        temp_path.unlink(missing_ok=True)
    return jsonify({"imported": [item.external_id for item in imported]})


def _spawn_worker(run_id: str) -> None:
    """Launch a detached subprocess that drives one run to completion.

    Routes must never execute pipeline code synchronously inside the Flask
    request/response cycle; this returns immediately after handing the run
    off to ``alfrd runtime execute`` in its own process.
    """
    database = current_app.config.get("RUNTIME_DATABASE")
    command = [sys.executable, "-m", "alfrd.cli", "runtime", "execute", run_id]
    if database:
        command.extend(["--db", str(database)])
    kwargs: dict = {}
    if os.name != "nt":
        kwargs["start_new_session"] = True
    else:  # pragma: no cover - Windows-only branch
        kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0)
    spawn = current_app.config.get("RUNTIME_SPAWN", subprocess.Popen)
    spawn(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **kwargs,
    )


def _run_dict(run) -> dict:
    return {
        "id": run.id,
        "workflow_id": run.workflow_id,
        "dataset_id": run.dataset_id,
        "parent_run_id": run.parent_run_id,
        "status": run.status,
        "attempt": run.attempt,
        "working_directory": run.working_directory,
        "parameters": run.parameters_json,
        "error": run.error,
        "pid": run.pid,
        "hostname": run.hostname,
        "steps": [
            {
                "id": step.id,
                "key": step.step_definition.key,
                "sequence": step.sequence,
                "attempt": step.attempt,
                "status": step.status,
                "exit_code": step.exit_code,
                "error": step.error,
            }
            for step in run.step_executions
        ],
    }


@control.post("/runs")
def start_run():
    """Start a workflow for one dataset via the runtime service only.

    Validates parameters and rejects a duplicate active run before
    persisting anything, then hands execution off to a spawned worker
    process rather than running the pipeline inline.
    """
    from alfrd.runtime import DuplicateRunError, ParameterValidationError

    service = _control_runtime_service()
    payload = request.get_json(silent=True) or {}
    workflow_id = payload.get("workflow_id")
    dataset_id = payload.get("dataset_id")
    dataset_ids = payload.get("dataset_ids")
    if not workflow_id or (not dataset_id and not dataset_ids):
        return jsonify(error={"code": 400, "message": "workflow_id and dataset_id(s) are required"}), 400
    parameters = payload.get("parameters")
    allow_concurrent = bool(payload.get("allow_concurrent", False))
    targets = dataset_ids if dataset_ids else [dataset_id]

    started = []
    errors = []
    for target in targets:
        try:
            run = service.start_run(
                workflow_id, target, parameters=parameters, allow_concurrent=allow_concurrent
            )
        except ParameterValidationError as error:
            errors.append({"dataset_id": target, "message": str(error)})
            continue
        except DuplicateRunError as error:
            errors.append({"dataset_id": target, "message": str(error)})
            continue
        _spawn_worker(run.id)
        started.append(_run_dict(run))

    status_code = 201 if started and not errors else (207 if started else 400)
    return jsonify(started=started, errors=errors), status_code


@control.get("/runs/<run_id>")
def get_run(run_id: str):
    from alfrd.runtime import RuntimeNotFound

    service = _control_runtime_service()
    try:
        run = service.get_run(run_id)
    except RuntimeNotFound as error:
        return jsonify(error={"code": 404, "message": str(error)}), 404
    return jsonify(_run_dict(run))


@control.get("/runs/<run_id>/logs")
def get_run_logs(run_id: str):
    from alfrd.runtime import RuntimeNotFound

    service = _control_runtime_service()
    try:
        logs = service.run_logs(run_id)
    except RuntimeNotFound as error:
        return jsonify(error={"code": 404, "message": str(error)}), 404
    return jsonify(logs=logs)


@control.get("/runs/<run_id>/audit")
def get_run_audit(run_id: str):
    service = _control_runtime_service()
    events = service.audit_events(run_id)
    return jsonify(events=[
        {
            "action": event.action,
            "from_status": event.from_status,
            "to_status": event.to_status,
            "payload": event.payload_json,
            "occurred_at": event.occurred_at.isoformat(),
        }
        for event in events
    ])


@control.post("/runs/<run_id>/resume")
def resume_run(run_id: str):
    from alfrd.runtime import InvalidTransition, RuntimeNotFound

    service = _control_runtime_service()
    try:
        run = service.resume_run(run_id)
    except RuntimeNotFound as error:
        return jsonify(error={"code": 404, "message": str(error)}), 404
    except InvalidTransition as error:
        return jsonify(error={"code": 409, "message": str(error)}), 409
    _spawn_worker(run.id)
    return jsonify(_run_dict(run))


@control.post("/runs/<run_id>/retry")
def retry_run(run_id: str):
    from alfrd.runtime import InvalidTransition, RuntimeNotFound

    service = _control_runtime_service()
    try:
        run = service.retry_run(run_id)
    except RuntimeNotFound as error:
        return jsonify(error={"code": 404, "message": str(error)}), 404
    except InvalidTransition as error:
        return jsonify(error={"code": 409, "message": str(error)}), 409
    _spawn_worker(run.id)
    return jsonify(_run_dict(run))


@control.post("/runs/<run_id>/steps/<step_key>/retry")
def retry_step(run_id: str, step_key: str):
    from alfrd.runtime import InvalidTransition, RuntimeNotFound

    service = _control_runtime_service()
    try:
        run = service.retry_step(run_id, step_key)
    except RuntimeNotFound as error:
        return jsonify(error={"code": 404, "message": str(error)}), 404
    except InvalidTransition as error:
        return jsonify(error={"code": 409, "message": str(error)}), 409
    _spawn_worker(run.id)
    return jsonify(_run_dict(run))


@control.post("/runs/<run_id>/cancel")
def cancel_run(run_id: str):
    """Cancel a run, requiring an explicit confirmation flag in the body.

    The browser client is responsible for prompting the user; this endpoint
    still refuses to cancel unless ``{"confirm": true}`` is present so a
    stray automated POST cannot cancel a run silently.
    """
    from alfrd.runtime import InvalidTransition, RuntimeNotFound

    service = _control_runtime_service()
    payload = request.get_json(silent=True) or {}
    if not payload.get("confirm"):
        return jsonify(
            error={"code": 400, "message": "cancellation requires {\"confirm\": true}"}
        ), 400
    try:
        run = service.cancel_run(run_id, reason=payload.get("reason"))
    except RuntimeNotFound as error:
        return jsonify(error={"code": 404, "message": str(error)}), 404
    except InvalidTransition as error:
        return jsonify(error={"code": 409, "message": str(error)}), 409
    return jsonify(_run_dict(run))
