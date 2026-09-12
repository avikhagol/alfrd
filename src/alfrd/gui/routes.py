from __future__ import annotations

import csv
import io
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


def _reader():
    return current_app.extensions["alfrd_catalog_reader"]


def _runtime_service():
    """The optional ``RuntimeService`` backing the sheet-like matrix.

    Returns ``None`` when the application was not configured with runtime
    persistence (e.g. the plain catalog-only deployment), so matrix routes
    can return a stable 404 instead of raising.
    """

    return current_app.config.get("RUNTIME_SERVICE")


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