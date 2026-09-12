from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from alfrd import __version__
from alfrd.gui import create_app
from alfrd.gui.model import db
from alfrd.gui.model.tables import (
    ArtifactDefinitionDB,
    DatasetColumnDB,
    ParameterDB,
    ProjectDB,
    StepDB,
    ValidatorDB,
    WorkflowDB,
)


@pytest.fixture()
def app(tmp_path: Path):
    application = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'catalog.db'}",
        }
    )
    with application.app_context():
        project = ProjectDB(
            name="demo",
            description="Read-only demo",
            manifest_path="/catalog/demo/alfrd.yaml",
            manifest_valid=False,
            manifest_errors=["workflows.build.steps[1]: unknown step"],
            sync_state="out_of_sync",
        )
        db.session.add(project)
        db.session.flush()
        workflow = WorkflowDB(
            project_id=project.id,
            name="build",
            description="Build workflow",
            sequence=["prepare"],
        )
        db.session.add(workflow)
        db.session.flush()
        step = StepDB(
            project_id=project.id,
            workflow_id=workflow.id,
            name="prepare",
            description="Prepare inputs",
            position=0,
        )
        db.session.add(step)
        db.session.flush()
        db.session.add_all(
            [
                ValidatorDB(
                    project_id=project.id,
                    step_id=step.id,
                    name="inputs_exist",
                    description="Inputs exist",
                    phase="before",
                    run_once=True,
                ),
                ParameterDB(
                    project_id=project.id,
                    step_id=step.id,
                    name="input_path",
                    required=True,
                    type_name="path",
                ),
                DatasetColumnDB(
                    project_id=project.id,
                    workflow_id=workflow.id,
                    name="source",
                    type_name="string",
                    required=True,
                ),
                ArtifactDefinitionDB(
                    project_id=project.id,
                    workflow_id=workflow.id,
                    name="report",
                    path_pattern="reports/{source}.json",
                    media_type="application/json",
                ),
            ]
        )
        db.session.commit()
    return application


@pytest.fixture()
def client(app):
    return app.test_client()


def test_health_and_version_endpoints(client):
    assert client.get("/health").get_json() == {"status": "ok"}
    assert client.get("/api/health").get_json() == {"status": "ok"}
    assert client.get("/version").get_json() == {"version": __version__}
    assert client.get("/api/version").get_json() == {"version": __version__}


def test_catalog_read_views(client):
    projects = client.get("/api/projects")
    assert projects.status_code == 200
    assert projects.get_json()["projects"][0]["name"] == "demo"

    project = client.get("/api/projects/demo")
    assert project.status_code == 200
    assert project.get_json()["description"] == "Read-only demo"

    workflow = client.get("/api/projects/demo/workflows/build")
    assert workflow.get_json()["sequence"] == ["prepare"]
    assert client.get("/api/projects/demo/workflows").get_json()["workflows"][0]["name"] == "build"

    step = client.get("/api/projects/demo/steps/prepare")
    assert step.get_json()["position"] == 0
    assert client.get("/api/projects/demo/steps").get_json()["steps"][0]["name"] == "prepare"

    validator = client.get("/api/projects/demo/validators/inputs_exist")
    assert validator.get_json()["phase"] == "before"
    assert client.get("/api/projects/demo/validators").get_json()["validators"][0]["run_once"] is True

    parameter = client.get("/api/projects/demo/parameters/input_path")
    assert parameter.get_json()["required"] is True
    assert client.get("/api/projects/demo/parameters").get_json()["parameters"][0]["type"] == "path"

    column = client.get("/api/projects/demo/dataset-columns/source")
    assert column.get_json()["type"] == "string"
    assert client.get("/api/projects/demo/dataset-columns").get_json()["dataset_columns"][0]["name"] == "source"

    artifact = client.get("/api/projects/demo/artifact-definitions/report")
    assert artifact.get_json()["media_type"] == "application/json"
    assert client.get("/api/projects/demo/artifact-definitions").get_json()["artifact_definitions"][0]["name"] == "report"


def test_manifest_validation_and_sync_are_presented(client):
    response = client.get("/api/projects/demo/manifest")
    assert response.status_code == 200
    assert response.get_json() == {
        "path": "/catalog/demo/alfrd.yaml",
        "sync": {"state": "out_of_sync"},
        "validation": {
            "errors": ["workflows.build.steps[1]: unknown step"],
            "valid": False,
        },
    }


def test_missing_project_is_resolved_before_child_lookup():
    class RecordingReader:
        def __init__(self):
            self.calls = []

        def get_project(self, name):
            self.calls.append(("get_project", name))
            return None

        def list_steps(self, project_id):
            raise AssertionError("child lookup must not run for a missing project")

    reader = RecordingReader()
    client = create_app({"TESTING": True, "CATALOG_READER": reader}).test_client()

    response = client.get("/api/projects/missing/steps")

    assert response.status_code == 404
    assert response.is_json
    assert response.get_json() == {
        "error": {"code": 404, "message": "Project 'missing' not found"}
    }
    assert reader.calls == [("get_project", "missing")]


def test_metadata_reads_never_import_project_python(tmp_path: Path):
    marker = tmp_path / "IMPORTED"
    project_dir = tmp_path / "home" / "projects" / "danger"
    project_dir.mkdir(parents=True)
    (project_dir / "plugin.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('imported')\n",
        encoding="utf-8",
    )
    application = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'safe.db'}",
        }
    )
    with application.app_context():
        db.session.add(ProjectDB(name="danger"))
        db.session.commit()

    response = application.test_client().get("/api/projects/danger")

    assert response.status_code == 200
    assert not marker.exists()


def test_all_catalog_and_dashboard_routes_are_get_only(app, client):
    read_paths = [
        "/api/projects",
        "/api/projects/demo",
        "/api/projects/demo/workflows",
        "/api/projects/demo/steps",
        "/api/projects/demo/validators",
        "/api/projects/demo/parameters",
        "/api/projects/demo/dataset-columns",
        "/api/projects/demo/artifact-definitions",
        "/api/projects/demo/manifest",
        "/dashboard/",
        "/dashboard/project/demo",
    ]
    for path in read_paths:
        assert client.post(path).status_code == 405, path


def test_schema_resource_matches_sqlalchemy_catalog(tmp_path: Path):
    schema = (
        Path(__file__).parents[1]
        / "src"
        / "alfrd"
        / "gui"
        / "model"
        / "schema.sql"
    ).read_text(encoding="utf-8")
    connection = sqlite3.connect(tmp_path / "schema.db")
    connection.executescript(schema)
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    assert tables == {
        "projects",
        "workflows",
        "steps",
        "validators",
        "parameters",
        "dataset_columns",
        "artifact_definitions",
    }


def test_dashboard_is_read_only_and_renders_catalog(app, client):
    index = client.get("/dashboard/")
    detail = client.get("/dashboard/project/demo")
    assert index.status_code == 200
    assert b"demo" in index.data
    assert detail.status_code == 200
    assert b"Manifest validation" in detail.data
    assert b"Run" not in detail.data
