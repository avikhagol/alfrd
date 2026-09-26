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


def _csrf(client) -> dict[str, str]:
    with client.session_transaction() as session:
        session["_alfrd_csrf_token"] = "known-token"
    return {"X-CSRF-Token": "known-token"}


def test_registers_existing_contained_artifact_without_writing_run_manifest(artifact_app):
    app, service, project, run, workdir = artifact_app
    product = workdir / "products" / "result.txt"
    product.parent.mkdir()
    product.write_text("result", encoding="utf-8")
    step = run.step_executions[0]
    client = app.test_client()

    response = client.post(
        "/dashboard/project/demo/artifacts",
        data={
            "run_id": run.id,
            "step_execution_id": step.id,
            "path": "products/result.txt",
            "name": "report",
            "media_type": "text/plain",
        },
        headers=_csrf(client),
    )

    assert response.status_code == 302
    persisted = service.get_run(run.id).artifacts[0]
    assert persisted.name == "report"
    assert persisted.step_execution_id == step.id
    assert not (workdir / ".alfrd" / "run.json").exists()


@pytest.mark.parametrize("relative", ["../outside.txt", ".env", "secrets/token.txt"])
def test_rejects_traversal_dotfiles_and_secrets(artifact_app, tmp_path, monkeypatch, relative):
    app, service, project, run, workdir = artifact_app
    (tmp_path / "outside.txt").write_text("no", encoding="utf-8")
    (workdir / ".env").write_text("secret", encoding="utf-8")
    (workdir / "secrets").mkdir()
    (workdir / "secrets" / "token.txt").write_text("secret", encoding="utf-8")
    monkeypatch.setattr("alfrd.gui.routes.render_template", lambda *a, **kw: kw)
    client = app.test_client()

    response = client.post(
        "/dashboard/project/demo/artifacts",
        data={"run_id": run.id, "path": relative},
        headers=_csrf(client),
    )

    assert response.status_code == 400
    assert service.get_run(run.id).artifacts == []


def test_rejects_symlink_escape_foreign_run_and_foreign_step(artifact_app, tmp_path, monkeypatch):
    app, service, project, run, workdir = artifact_app
    outside = tmp_path / "outside.txt"
    outside.write_text("no", encoding="utf-8")
    (workdir / "link.txt").symlink_to(outside)
    other_project = service.create_project("other", tmp_path / "other")
    other_workflow = service.create_workflow(
        other_project.id, "wf", [{"key": "two", "command": ["true"]}]
    )
    other_dataset = service.create_dataset(other_project.id, "other")
    other_run = service.create_run(other_workflow.id, other_dataset.id)
    good = workdir / "good.txt"
    good.write_text("ok", encoding="utf-8")
    monkeypatch.setattr("alfrd.gui.routes.render_template", lambda *a, **kw: kw)
    client = app.test_client()
    headers = _csrf(client)

    assert client.post(
        "/dashboard/project/demo/artifacts",
        data={"run_id": run.id, "path": "link.txt"}, headers=headers
    ).status_code == 400
    assert client.post(
        "/dashboard/project/demo/artifacts",
        data={"run_id": other_run.id, "path": "good.txt"}, headers=headers
    ).status_code == 400
    assert client.post(
        "/dashboard/project/demo/artifacts",
        data={"run_id": run.id, "step_execution_id": other_run.step_executions[0].id, "path": "good.txt"},
        headers=headers,
    ).status_code == 400


def test_execution_detail_is_scoped_to_url_project(artifact_app):
    app, service, project, run, workdir = artifact_app
    other = service.create_project("other", workdir / "other")
    client = app.test_client()
    response = client.get(f"/api/projects/other/executions/{run.step_executions[0].id}")
    assert response.status_code == 404
