"""Safe project removal: preview, Forget (database only) and the disabled permanent delete."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from alfrd.gui import create_app
from alfrd.runtime import RuntimeService, RuntimeStore
from alfrd.runtime.scheduler import PLANS_DIR

TOKEN = "test-token"
H = {"X-CSRF-Token": TOKEN}


@pytest.fixture()
def service(tmp_path: Path) -> RuntimeService:
    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    return RuntimeService(store)


@pytest.fixture()
def project(service: RuntimeService, tmp_path: Path):
    root = tmp_path / "demo"
    root.mkdir()
    (root / "data.txt").write_text("keep me\n")
    row = service.create_project("demo", root)
    wf = service.create_workflow(row.id, "wf", [{"key": "one", "command": [sys.executable, "-c", "print('ok')"]}])
    ds_a = service.create_dataset(row.id, "a")
    service.create_dataset(row.id, "b")
    service.create_run(wf.id, ds_a.id)
    return service.get_project_by_selector(row.identifier), root


def _client(service, tmp_path, **config):
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'catalog.sqlite'}",
        "RUNTIME_SERVICE": service,
        **config,
    })
    client = app.test_client()
    with client.session_transaction() as session:
        session["_alfrd_csrf_token"] = TOKEN
    return client


@pytest.fixture()
def client(service, tmp_path):
    return _client(service, tmp_path)


def _fake_plan(root: Path, status: str = "running") -> None:
    folder = root / PLANS_DIR / "20261003-000000-0000-abcdef01"
    folder.mkdir(parents=True)
    (folder / "plan.json").write_text(json.dumps({"id": folder.name, "status": status, "created": "2026-10-03T00:00:00"}))


def _files(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*")}


def test_preview_counts_and_no_delete_scope(client, project):
    row, root = project
    for selector in (row.identifier, row.name):
        res = client.get(f"/api/studio/projects/{selector}/removal-preview")
        assert res.status_code == 200
        data = res.get_json()
        assert data["identifier"] == row.identifier and data["name"] == "demo"
        assert data["root_path"] == row.root_path
        assert data["counts"] == {"runs": 1, "datasets": 2, "workflows": 1}
        assert data["active_jobs"] == [] and data["can_remove"] is True and data["reason"] is None
        assert data["mutations_enabled"] is True
        assert data["delete_scope"] is None
    assert client.get("/api/studio/projects/nope/removal-preview").status_code == 404


def test_forget_blocked_with_409_while_a_plan_is_active(client, project, service):
    row, root = project
    _fake_plan(root, "running")
    data = client.get(f"/api/studio/projects/{row.identifier}/removal-preview").get_json()
    assert data["can_remove"] is False
    assert data["reason"] == "Stop this project's active runs before removing it."
    assert [j["kind"] for j in data["active_jobs"]] == ["plan"]
    res = client.post(f"/api/studio/projects/{row.identifier}/forget", headers=H)
    assert res.status_code == 409
    assert res.get_json()["error"]["message"] == "Stop this project's active runs before removing it."
    assert service.get_project_by_selector(row.identifier).identifier == row.identifier  # still there


def test_finished_plan_does_not_block(client, project):
    row, root = project
    _fake_plan(root, "finished")
    assert client.get(f"/api/studio/projects/{row.identifier}/removal-preview").get_json()["can_remove"] is True


def test_forget_touches_database_only(client, project, service):
    row, root = project
    before = _files(root)
    client.application.config["STUDIO_PROJECTS"] = [row.identifier, "other"]
    res = client.post(f"/api/studio/projects/{row.identifier}/forget", headers=H)
    assert res.status_code == 200
    body = res.get_json()
    assert body["runs"] == 1 and body["datasets"] == 2 and body["workflows"] == 1
    assert _files(root) == before and (root / "data.txt").read_text() == "keep me\n"
    assert client.application.config["STUDIO_PROJECTS"] == ["other"]
    assert client.get(f"/api/studio/projects/{row.identifier}/removal-preview").status_code == 404


def test_forget_by_name_clears_identifier_scope(client, project):
    row, root = project
    client.application.config["STUDIO_PROJECTS"] = [row.identifier]
    assert client.post(f"/api/studio/projects/{row.name}/forget", headers=H).status_code == 200
    assert client.application.config["STUDIO_PROJECTS"] == []
    assert root.is_dir()


def test_delete_refused_without_deletion_scope(client, project, service):
    row, root = project
    before = _files(root)
    res = client.post(f"/api/studio/projects/{row.identifier}/delete", headers=H, json={"confirm": "demo"})
    assert res.status_code == 409
    assert res.get_json()["error"]["message"] == "Deletion scope has not been configured"
    assert _files(root) == before
    assert service.get_project_by_selector(row.identifier).identifier == row.identifier


def test_delete_and_forget_need_mutation_permission(service, project, tmp_path):
    row, root = project
    before = _files(root)
    readonly = _client(service, tmp_path, RUNTIME_MUTATIONS_ENABLED=False)
    preview = readonly.get(f"/api/studio/projects/{row.identifier}/removal-preview").get_json()
    assert preview["mutations_enabled"] is False and preview["can_remove"] is False
    assert preview["reason"] == "Project removal is available only in a writable local Studio session."
    for action in ("forget", "delete"):
        assert readonly.post(f"/api/studio/projects/{row.identifier}/{action}", headers=H).status_code == 403
    # Writable server, but no CSRF token: refused as well.
    writable = _client(service, tmp_path)
    for action in ("forget", "delete"):
        assert writable.post(f"/api/studio/projects/{row.identifier}/{action}").status_code == 403
    assert _files(root) == before
    assert service.get_project_by_selector(row.identifier).identifier == row.identifier
