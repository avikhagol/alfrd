from __future__ import annotations

import sys
from pathlib import Path

import pytest

from alfrd.gui import create_app
from alfrd.gui.services import RuntimeCatalogReader
from alfrd.runtime import RuntimeService, RuntimeStore


@pytest.fixture
def artifact_app(tmp_path: Path):
    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    service = RuntimeService(store)
    project = service.create_project("demo", tmp_path / "project")
    workflow = service.create_workflow(
        project.id, "wf", [{"key": "one", "command": [sys.executable, "-V"]}]
    )
    dataset = service.create_dataset(project.id, "dataset")
    workdir = tmp_path / "external-run"
    workdir.mkdir()
    run = service.import_historical_run(
        workflow.id,
        dataset.id,
        working_directory=workdir,
        status="succeeded",
        executions=[{"key": "one", "status": "succeeded"}],
    )
    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'catalog.sqlite'}",
            "RUNTIME_SERVICE": service,
            "CATALOG_READER": RuntimeCatalogReader(service),
        }
    )
    return app, service, project, run, workdir


def test_execution_detail_is_scoped_to_url_project(artifact_app):
    app, service, project, run, workdir = artifact_app
    other = service.create_project("other", workdir / "other")
    client = app.test_client()
    response = client.get(f"/api/projects/other/executions/{run.step_executions[0].id}")
    assert response.status_code == 404
