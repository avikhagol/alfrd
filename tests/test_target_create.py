"""The header's New target form: ``GET/POST /api/studio/projects/<p>/targets/{new,add}``."""

from __future__ import annotations

from pathlib import Path

import pytest

from alfrd import targets_csv as tc
from alfrd.project_creation import create_project
from alfrd.runtime import RuntimeService, RuntimeStore

pytest.importorskip("flask")


@pytest.fixture
def service(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    return RuntimeService(store)


def _client(service, tmp_path):
    from alfrd.gui import create_app
    from alfrd.gui.services import RuntimeCatalogReader

    app = create_app({"TESTING": True, "SECRET_KEY": "k", "RUNTIME_SERVICE": service,
                      "CATALOG_READER": RuntimeCatalogReader(service),
                      "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'catalog.sqlite'}"})
    client = app.test_client()
    headers = {"X-CSRF-Token": client.get("/api/studio/session").get_json()["csrf_token"]}
    return client, headers, f"/api/studio/projects/{service.list_projects()[0].identifier}"


def test_basic_project_adds_a_targets_file_row(service, tmp_path):
    root = tmp_path / "basic"
    create_project(service, root, template="basic")
    client, h, prefix = _client(service, tmp_path)

    info = client.get(prefix + "/targets/new").get_json()
    assert info == {"kind": "target", "needs_task": False, "file": "alfrd.targets.csv"}

    assert client.post(prefix + "/targets/add", json={"name": "NGC1"}).status_code == 403  # CSRF gate
    made = client.post(prefix + "/targets/add", json={"name": " NGC1 ", "code": "ab12"}, headers=h)
    assert made.status_code == 201 and made.get_json() == {"name": "NGC1", "kind": "target", "file": "alfrd.targets.csv"}
    rows = tc.read(tc.load_spec(root))["rows"]
    assert [(r["target"], r["code"]) for r in rows] == [("NGC1", "ab12")]

    again = client.post(prefix + "/targets/add", json={"name": "NGC1"}, headers=h)
    assert again.status_code == 409 and "already exists" in again.get_json()["error"]["message"]
    for bad in ("", "#x", "a@b", "a/b"):
        assert client.post(prefix + "/targets/add", json={"name": bad}, headers=h).status_code == 400, bad
    assert len(tc.read(tc.load_spec(root))["rows"]) == 1


def test_agent_loop_project_creates_a_task_folder(service, tmp_path):
    root = tmp_path / "loop"
    create_project(service, root, template="agent-loop", task="Fix the widget", iterations=2)
    client, h, prefix = _client(service, tmp_path)

    info = client.get(prefix + "/targets/new").get_json()
    assert info["kind"] == "task" and info["needs_task"] is True

    missing = client.post(prefix + "/targets/add", json={"name": "second"}, headers=h)
    assert missing.status_code == 400 and not (root / "second").exists()
    made = client.post(prefix + "/targets/add", json={"name": "second", "task": "Add a feature"}, headers=h)
    assert made.status_code == 201 and made.get_json()["kind"] == "task"
    assert (root / "second" / "task.md").read_text() == "Add a feature\n"
    assert "second" in Path(root / "alfrd.plan.csv").read_text()
    assert client.post(prefix + "/targets/add", json={"name": "second", "task": "x"}, headers=h).status_code == 409
    names = [t["name"] for t in client.get(prefix + "/tasks").get_json()["tasks"]]
    assert "second" in names
