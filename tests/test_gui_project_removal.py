"""Safe project removal: preview, Forget (database only) and permanent deletion."""

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
        scope = data["delete_scope"]
        assert scope["all_files_allowed"] is True and scope["path"] == str(root.resolve())
        assert scope["measured"] is False and scope["files"] is None and scope["git_repository"] is False
        files = len([p for p in root.rglob("*") if p.is_file()])
        for url in (f"/api/studio/projects/{selector}/removal-size", f"/api/studio/projects/{selector}/removal-preview?size=1"):
            body = client.get(url).get_json()
            size = body.get("delete_scope", body)
            assert size["measured"] is True and size["files"] == files >= 1
            assert size["bytes"] >= len("keep me\n") and size["truncated"] is False
    assert client.get("/api/studio/projects/nope/removal-preview").status_code == 404
    assert client.get("/api/studio/projects/nope/removal-size").status_code == 404


def test_preview_forget_and_alfrd_only_delete_never_walk_the_folder(client, project, monkeypatch):
    import os

    row, root = project
    walks: list[str] = []
    real_walk = os.walk
    monkeypatch.setattr(os, "walk", lambda top, *a, **k: walks.append(str(top)) or real_walk(top, *a, **k))
    assert client.get(f"/api/studio/projects/{row.identifier}/removal-preview").status_code == 200
    assert client.post(f"/api/studio/projects/{row.identifier}/forget", headers=H).status_code == 200
    assert walks == []
    again = client.application.config["RUNTIME_SERVICE"].create_project("demo", root)
    res = client.post(f"/api/studio/projects/{again.identifier}/delete", headers=H, json={"confirm": "demo"})
    assert res.status_code == 200 and walks == []


def test_delete_all_files_walks_at_most_once(client, project, monkeypatch):
    import os

    row, root = project
    walks: list[str] = []
    real_walk = os.walk
    monkeypatch.setattr(os, "walk", lambda top, *a, **k: walks.append(str(top)) or real_walk(top, *a, **k))
    res = client.post(f"/api/studio/projects/{row.identifier}/delete", headers=H, json={"confirm": "demo", "all_files": True})
    assert res.status_code == 200 and res.get_json()["files"] >= 1 and not root.exists()
    assert walks.count(str(root.resolve())) <= 1


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


def _alfrd_project(root: Path) -> None:
    """ALFRD's files next to the user's own ones."""
    (root / "alfrd.yaml").write_text("name: demo\n")
    (root / "alfrd.yaml.bak").write_text("name: old\n")
    (root / "alfrd.plan.csv").write_text("TARGET_NAME\n")
    (root / "alfrd.notes.jsonl").write_text("{}\n")
    (root / ".alfrd" / "history").mkdir(parents=True)
    (root / "task").mkdir()
    (root / "task" / "task.md").write_text("my task\n")
    (root / "task" / ".alfrd-task.json").write_text("{}")


def test_delete_needs_the_exact_name_and_removes_only_alfrd_files(client, project, service):
    from alfrd.runtime import RuntimeNotFound
    row, root = project
    _alfrd_project(root)
    scope = client.get(f"/api/studio/projects/{row.identifier}/removal-preview").get_json()["delete_scope"]
    assert set(scope["alfrd_files"]) >= {"alfrd.yaml", "alfrd.yaml.bak", "alfrd.plan.csv", "alfrd.notes.jsonl", ".alfrd/", "task/.alfrd-task.json"}
    before = _files(root)
    for wrong in (None, "Demo", "demo "):
        res = client.post(f"/api/studio/projects/{row.identifier}/delete", headers=H, json={"confirm": wrong})
        assert res.status_code == 400 and _files(root) == before
    res = client.post(f"/api/studio/projects/{row.identifier}/delete", headers=H, json={"confirm": "demo"})
    assert res.status_code == 200, res.get_json()
    body = res.get_json()
    assert body["all_files"] is False and "alfrd.yaml" in body["removed"] and body["runs"] == 1
    assert sorted(p.relative_to(root).as_posix() for p in root.rglob("*")) == ["data.txt", "task", "task/task.md"]
    with pytest.raises(RuntimeNotFound):
        service.get_project_by_selector(row.identifier)


def test_delete_all_files_removes_the_folder(client, project, service):
    row, root = project
    _alfrd_project(root)
    res = client.post(f"/api/studio/projects/{row.identifier}/delete", headers=H, json={"confirm": "demo", "all_files": True})
    assert res.status_code == 200 and res.get_json()["all_files"] is True
    assert not root.exists()


def test_alfrd_only_delete_keeps_global_state_in_dot_alfrd(service, tmp_path):
    client = _client(service, tmp_path)
    root = tmp_path / "shared"
    (root / ".alfrd" / "plans").mkdir(parents=True)
    (root / ".alfrd" / "search").mkdir()
    service.store.database = str(root / ".alfrd" / "runtime.sqlite")
    (root / ".alfrd" / "runtime.sqlite").write_text("db")
    row = service.create_project("shared", root)
    res = client.post(f"/api/studio/projects/{row.identifier}/delete", headers=H, json={"confirm": "shared"})
    assert res.status_code == 200
    assert not (root / ".alfrd" / "plans").exists()
    assert (root / ".alfrd" / "runtime.sqlite").exists() and (root / ".alfrd" / "search").is_dir()


def test_delete_blocked_while_a_plan_is_active(client, project):
    row, root = project
    _fake_plan(root, "running")
    res = client.post(f"/api/studio/projects/{row.identifier}/delete", headers=H, json={"confirm": "demo"})
    assert res.status_code == 409 and root.is_dir()


@pytest.mark.parametrize("case", ["home", "database", "nested", "symlink", "toplevel"])
def test_delete_all_files_refuses_unsafe_folders(service, tmp_path, monkeypatch, case):
    client = _client(service, tmp_path)
    root = tmp_path / "outer"
    root.mkdir()
    (root / "f.txt").write_text("x")
    name = "outer"
    if case == "home":
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: root / "me"))
    elif case == "database":
        service.store.database = str(root / "runtime.sqlite")
    elif case == "nested":
        service.create_project("inner", root / "inner")
    elif case == "symlink":
        link = tmp_path / "link"
        link.symlink_to(root, target_is_directory=True)
        root, name = link, "link"
    row = service.create_project(name, root) if case != "toplevel" else service.create_project("tmp", Path("/tmp"))
    if case == "symlink":  # create_project resolves paths; record the link itself
        with service.store.session() as session:
            from alfrd.runtime.models import Project
            session.get(Project, row.id).root_path = str(root)
    data = client.get(f"/api/studio/projects/{row.identifier}/removal-preview").get_json()
    assert data["delete_scope"]["all_files_allowed"] is False and data["delete_scope"]["all_files_reason"]
    res = client.post(f"/api/studio/projects/{row.identifier}/delete", headers=H, json={"confirm": row.name, "all_files": True})
    assert res.status_code == 409
    assert (tmp_path / "outer" / "f.txt").exists()


def test_delete_of_a_missing_folder_only_forgets(client, service, tmp_path):
    row = service.create_project("gone", tmp_path / "gone")
    (tmp_path / "gone").rmdir()
    res = client.post(f"/api/studio/projects/{row.identifier}/delete", headers=H, json={"confirm": "gone"})
    assert res.status_code == 200 and res.get_json()["files"] == 0


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
