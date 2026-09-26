from __future__ import annotations

import csv
import io
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from alfrd import __version__
from alfrd.runtime import RuntimeNotFound
from alfrd.runtime.matrix import (
    DETAIL_FIELDS,
    MATRIX_STATUSES,
    MatrixQueryService,
    cell_detail,
)
from alfrd.gui.services import resolve_artifact_path, resolve_selected_manifest
from alfrd.gui.summaries import build_dataset_summary, build_summaries
from alfrd.manifest import ManifestError, ManifestNotFoundError, load_manifest


api = Blueprint("api", __name__, url_prefix="/api")
dashboard = Blueprint("dashboard", __name__, url_prefix="/dashboard")
system = Blueprint("system", __name__)
control = Blueprint("control", __name__, url_prefix="/api/runtime")
_SAFE_PROJECT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}\Z")


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


@system.get("/")
def root():
    """The client-side ALFRD Studio is the default UI; /dashboard/ remains."""
    from alfrd.gui.studio import studio_available

    if studio_available():
        return redirect(url_for("studio.studio_index"))
    return redirect(url_for("dashboard.index_dashboard"))


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


def _connect_form(path: str = "", errors: list[str] | None = None, status: int = 200):
    return (
        render_template(
            "dashboard/connect.htm",
            title="Connect project",
            form={"path": path},
            errors=list(errors or []),
        ),
        status,
    )


def connect_manifest_path(path: str):
    """Validate and register a project directory/manifest with the runtime.

    Returns ``(project_dict, errors, status)``; shared by the HTML form and the
    Studio's JSON endpoint so both enforce identical rules.
    """
    used_default = False
    try:
        try:
            manifest_path = resolve_selected_manifest(path)
        except ManifestNotFoundError:
            # A folder without alfrd.yaml: use the default manifest (name = folder
            # name), like `alfrd serve` in such a folder. A local alfrd.yaml saved
            # later from Project settings replaces it.
            manifest_path, manifest = _default_manifest_for(path)
            used_default = True
        else:
            manifest = load_manifest(manifest_path)
            if _SAFE_PROJECT_NAME.fullmatch(manifest.name) is None:
                raise ValueError(
                    "Project name must start with a letter or digit and contain only "
                    "letters, digits, dots, underscores, or hyphens."
                )
    except (ManifestError, OSError, ValueError) as error:
        return None, [str(error)], 400

    service = _runtime_service()
    if service is None:  # also enforced by the global mutation gate
        abort(403, description="runtime mutations are disabled")
    from alfrd.runtime.identity import project_identifier

    root_path = manifest_path.parent.resolve()  # the folder itself for the default manifest
    identifier = project_identifier(root_path, manifest.name)
    try:
        existing = service.get_project_by_identifier(identifier)
    except RuntimeNotFound:
        existing = None
    if existing is None:
        try:
            service.register_manifest(manifest, root_path=root_path, create_root=False)
        except Exception as error:
            # Database uniqueness errors can still occur if another request
            # connects the same name concurrently; present them as conflicts.
            return None, [f"Could not connect project: {error}"], 409
    project = _reader().get_project(identifier) or {
        "identifier": identifier, "name": manifest.name,
        "display_name": manifest.name, "root_path": str(root_path),
    }
    project = {**project, "default_manifest": used_default}
    # `alfrd serve` shows a scoped list (STUDIO_PROJECTS): a connected project
    # joins it on the server too, so it is still there after a page reload.
    scope = current_app.config.get("STUDIO_PROJECTS")
    if isinstance(scope, list) and identifier not in scope:
        scope.append(identifier)
    return project, [], 200


def _default_manifest_for(path: str):
    """``(folder/alfrd.yaml, manifest)`` for a folder without its own alfrd.yaml.

    The returned path does not exist; only its parent (the folder) is used.
    Raises ManifestNotFoundError when no default manifest is available.
    """
    from alfrd.manifest import parse_manifest
    from alfrd.manifest_default import default_manifest_data, default_manifest_path

    folder = Path(path).expanduser().resolve(strict=True)
    default = default_manifest_path()
    if default is None:
        raise ManifestNotFoundError(f"No alfrd.yaml in {folder} and no default alfrd.yaml (ALFRD_DEFAULT_MANIFEST)")
    manifest = parse_manifest(default_manifest_data(folder), source=default)
    return folder / "alfrd.yaml", manifest


@dashboard.route("/connect", methods=["GET", "POST"])
def connect_project():
    if request.method == "GET":
        return _connect_form()
    path = request.form.get("path", "").strip()
    project, errors, status = connect_manifest_path(path)
    if errors:
        return _connect_form(path, errors, status)
    flash(f"Project {project['name']!r} connected.", "success")
    return redirect(url_for("dashboard.project_details", project_name=project.get("identifier", project["name"])))


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
        summaries=build_summaries(project, _runtime_service()),
    )


def _project_runs(project_id: str) -> list[dict]:
    service = _runtime_service()
    if service is None:
        return []
    datasets = {item.id: item.external_id for item in service.list_datasets(project_id)}
    return [
        {
            "id": run.id,
            "label": f"{datasets.get(run.dataset_id, run.dataset_id)} — {run.status}",
            "working_directory": run.working_directory,
            "steps": [
                {"id": step.id, "label": step.step_definition.key}
                for step in run.step_executions
            ],
        }
        for run in service.list_runs(project_id=project_id)
    ]


def _artifact_rows(project_id: str) -> list[dict]:
    service = _runtime_service()
    if service is None:
        return []
    return [
        {
            "id": artifact.id,
            "name": artifact.name,
            "path": artifact.path,
            "run_id": run.id,
            "step_execution_id": artifact.step_execution_id,
            "media_type": artifact.media_type,
            "size_bytes": artifact.size_bytes,
        }
        for run in service.list_runs(project_id=project_id)
        for artifact in run.artifacts
    ]


def _artifact_form(project: dict, form: dict[str, str], errors=None, status: int = 200):
    return (
        render_template(
            "dashboard/artifacts.htm",
            title=f"{project['name']} artifacts",
            project=project,
            runs=_project_runs(project["id"]),
            artifacts=_artifact_rows(project["id"]),
            form=form,
            errors=list(errors or []),
        ),
        status,
    )


@dashboard.route("/project/<project_name>/artifacts", methods=["GET", "POST"])
def project_artifacts(project_name: str):
    project = _project_or_404(project_name)
    runs = _project_runs(project["id"])
    owned_runs = {item["id"]: item for item in runs}
    if request.method == "GET":
        run_id = request.args.get("run_id", "")
        if run_id not in owned_runs:
            run_id = ""
        step_id = request.args.get("step_execution_id", "")
        if not run_id or step_id not in {step["id"] for step in owned_runs[run_id]["steps"]}:
            step_id = ""
        return _artifact_form(
            project,
            {"run_id": run_id, "step_execution_id": step_id, "path": "", "name": "", "media_type": ""},
        )

    form = {
        key: request.form.get(key, "").strip()
        for key in ("run_id", "step_execution_id", "path", "name", "media_type")
    }
    errors: list[str] = []
    selected = owned_runs.get(form["run_id"])
    if selected is None:
        errors.append("Select a run belonging to this project.")
    step_ids = {step["id"] for step in selected["steps"]} if selected else set()
    if form["step_execution_id"] and form["step_execution_id"] not in step_ids:
        errors.append("Select a step belonging to the selected run.")
    if len(form["name"]) > 255:
        errors.append("Artifact name must be at most 255 characters.")
    if len(form["media_type"]) > 255:
        errors.append("Media type must be at most 255 characters.")
    artifact_path = None
    if selected is not None:
        try:
            artifact_path = resolve_artifact_path(selected["working_directory"], form["path"])
        except ValueError as error:
            errors.append(str(error))
    elif not form["path"]:
        errors.append("Artifact path is required.")
    if errors:
        return _artifact_form(project, form, errors, 400)

    service = _runtime_service()
    assert service is not None and artifact_path is not None
    try:
        service.record_artifact(
            form["run_id"],
            artifact_path,
            step_execution_id=form["step_execution_id"] or None,
            name=form["name"] or None,
            media_type=form["media_type"] or None,
            write_manifest=False,
        )
    except (RuntimeNotFound, OSError, ValueError) as error:
        return _artifact_form(project, form, [str(error)], 400)
    flash("Artifact registered.", "success")
    return redirect(url_for("dashboard.project_artifacts", project_name=project_name))


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


@api.get("/projects/<project_name>/workflows/<workflow_name>/datasets/<dataset_id>")
def dataset_result_api(project_name: str, workflow_name: str, dataset_id: str):
    service = _runtime_service()
    if service is None:
        abort(404, description="dataset results require a configured runtime service")
    project_id, workflow_id = _workflow_id_or_404(project_name, workflow_name)
    try:
        return jsonify(build_dataset_summary(service, project_id, workflow_id, dataset_id))
    except (RuntimeNotFound, ValueError) as error:
        abort(404, description=str(error))


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
    project = _project_or_404(project_name)
    service = _runtime_service()
    if service is None:
        abort(404, description="the sheet-like matrix requires a configured runtime service")
    try:
        execution = service.get_execution(execution_id)
        run = service.get_run(execution.run_id)
        workflow = service.get_workflow(run.workflow_id)
        if workflow.project_id != project["id"]:
            abort(404, description=f"execution {execution_id!r} not found in project")
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
