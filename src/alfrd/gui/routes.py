from __future__ import annotations

from flask import Blueprint, abort, current_app, render_template

from alfrd import __version__


api = Blueprint("api", __name__, url_prefix="/api")
dashboard = Blueprint("dashboard", __name__, url_prefix="/dashboard")
system = Blueprint("system", __name__)


def _reader():
    return current_app.extensions["alfrd_catalog_reader"]


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