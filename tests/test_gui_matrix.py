from __future__ import annotations

import csv
import io
import sys
from pathlib import Path

import pytest

from alfrd.gui import create_app
from alfrd.runtime import RuntimeService, RuntimeStore, Status


@pytest.fixture
def runtime(tmp_path: Path):
    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    service = RuntimeService(store)
    project = service.create_project("demo", tmp_path / "demo")
    workflow = service.create_workflow(
        project.id,
        "reduce",
        [
            {"key": "prepare", "command": [sys.executable, "-c", "print('ok')"]},
            {"key": "finish", "command": [sys.executable, "-c", "print('done')"]},
        ],
    )
    queued = service.create_dataset(project.id, "queued-1")
    active = service.create_dataset(project.id, "active-1")
    run = service.create_run(workflow.id, active.id)
    service.transition_run(run.id, Status.RUNNING)
    execution = service.next_pending_execution(run.id)
    service.transition_execution(execution.id, Status.RUNNING)
    return service, project, workflow


@pytest.fixture
def app(tmp_path: Path, runtime):
    service, project, workflow = runtime
    from alfrd.gui.services import RuntimeCatalogReader

    application = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'catalog.db'}",
            "CATALOG_READER": RuntimeCatalogReader(service),
            "RUNTIME_SERVICE": service,
        }
    )
    return application


@pytest.fixture
def client(app):
    return app.test_client()


def test_matrix_page_renders_rows_and_summary(client, runtime):
    _, project, workflow = runtime
    response = client.get(f"/dashboard/project/demo/workflows/{workflow.name}/matrix")
    assert response.status_code == 200
    assert b"queued-1" in response.data
    assert b"active-1" in response.data
    assert b"prepare" in response.data
    assert b"finish" in response.data


def test_matrix_json_endpoint_returns_statuses(client, runtime):
    _, project, workflow = runtime
    response = client.get(f"/api/projects/demo/workflows/{workflow.name}/matrix")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["steps"] == ["prepare", "finish"]
    by_dataset = {row["dataset_external_id"]: row for row in payload["rows"]}
    assert by_dataset["queued-1"]["run_status"] == "queued"
    assert by_dataset["active-1"]["run_status"] == "running"
    assert payload["summary"]["total_datasets"] == 2


def test_matrix_json_endpoint_filters_by_status_and_search(client, runtime):
    _, project, workflow = runtime
    response = client.get(
        f"/api/projects/demo/workflows/{workflow.name}/matrix",
        query_string={"status": "queued"},
    )
    payload = response.get_json()
    assert [row["dataset_external_id"] for row in payload["rows"]] == ["queued-1"]

    response = client.get(
        f"/api/projects/demo/workflows/{workflow.name}/matrix",
        query_string={"search": "active"},
    )
    payload = response.get_json()
    assert [row["dataset_external_id"] for row in payload["rows"]] == ["active-1"]


def test_matrix_cell_detail_endpoint(client, runtime):
    service, project, workflow = runtime
    run = service.list_runs(project_id=project.id)[0]
    execution = run.step_executions[0]
    response = client.get(f"/api/projects/demo/executions/{execution.id}")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["execution_id"] == execution.id
    assert payload["step"] == "prepare"


def test_matrix_csv_export_endpoints(client, runtime):
    _, project, workflow = runtime
    response = client.get(f"/api/projects/demo/workflows/{workflow.name}/matrix.csv")
    assert response.status_code == 200
    assert response.mimetype == "text/csv"
    rows = list(csv.DictReader(io.StringIO(response.data.decode())))
    assert {row["dataset_id"] for row in rows} == {"queued-1", "active-1"}

    detail_response = client.get(
        f"/api/projects/demo/workflows/{workflow.name}/matrix-details.csv"
    )
    assert detail_response.status_code == 200
    assert detail_response.mimetype == "text/csv"


def test_matrix_dataset_csv_import_endpoint(client, runtime):
    _, project, workflow = runtime
    payload = "external_id,name,uri,metadata\nimported-1,Imported,,{}\n"
    response = client.post(
        f"/api/projects/demo/datasets/import",
        data={"file": (io.BytesIO(payload.encode()), "datasets.csv")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    matrix = client.get(
        f"/api/projects/demo/workflows/{workflow.name}/matrix"
    ).get_json()
    assert "imported-1" in {row["dataset_external_id"] for row in matrix["rows"]}


def test_matrix_routes_are_read_only_except_import(client, runtime):
    _, project, workflow = runtime
    read_paths = [
        f"/dashboard/project/demo/workflows/{workflow.name}/matrix",
        f"/api/projects/demo/workflows/{workflow.name}/matrix",
        f"/api/projects/demo/workflows/{workflow.name}/matrix.csv",
        f"/api/projects/demo/workflows/{workflow.name}/matrix-details.csv",
    ]
    for path in read_paths:
        assert client.post(path).status_code == 405, path
