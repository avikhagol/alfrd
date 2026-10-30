"""The shown-project selection survives a fresh Studio server."""

import json

import pytest

from alfrd.gui import create_app
from alfrd.gui.services import RuntimeCatalogReader
from alfrd.manifest_default import register_project_folder
from alfrd.runtime import RuntimeService, RuntimeStore


@pytest.fixture
def studio(tmp_path):
    database = tmp_path / "runtime.sqlite"
    store = RuntimeStore(database)
    store.initialize()
    service = RuntimeService(store)

    def register(name):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "alfrd.yaml").write_text(f"name: {name}\n")
        return register_project_folder(service, folder)[0].identifier

    first, second = register("first"), register("second")

    def start(**overrides):
        app = create_app({
            "TESTING": True, "SECRET_KEY": "test",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'catalog.sqlite'}",
            "RUNTIME_SERVICE": service, "CATALOG_READER": RuntimeCatalogReader(service),
            **overrides,
        })
        client = app.test_client()
        session = client.get("/api/studio/session").get_json()
        return app, client, {"X-CSRF-Token": session["csrf_token"]}

    yield start, register, first, second, tmp_path / "runtime.sqlite.studio-projects.json", service
    store.close()


def test_save_restore_and_new_project_remains_selectable(studio):
    start, register, first, second, path, _ = studio
    _, client, headers = start()
    assert client.get("/api/studio/session").get_json()["projects"] is None
    assert client.post(f"/api/studio/projects/{second}/visibility", json={"state": "hidden"}, headers=headers).status_code == 200
    assert json.loads(path.read_text()) == {"version": 1, "shown": [first]}

    new = register("new")
    # A fresh server restores the selection even when startup discovered another project.
    _, client, headers = start(STUDIO_PROJECTS=[second], STUDIO_DEFAULT_PROJECT=second)
    session = client.get("/api/studio/session").get_json()
    assert session["projects"] == [first]
    assert session["default_project"] is None
    # Settings uses the unfiltered catalog, including hidden and newly added projects.
    assert {p["identifier"] for p in client.get("/api/projects").get_json()["projects"]} == {first, second, new}
    response = client.post(f"/api/studio/projects/{new}/visibility", json={"state": "shown"}, headers=headers)
    assert response.get_json()["projects"] == [first, new]
    _, client, _ = start()
    assert client.get("/api/studio/session").get_json()["projects"] == [first, new]


@pytest.mark.parametrize("saved", [[], ["missing"], None])
def test_empty_or_missing_projects_fall_back_to_all(studio, saved):
    start, _, _, _, path, _ = studio
    path.write_text(json.dumps({"version": 1, "shown": saved}))
    _, client, _ = start()
    assert client.get("/api/studio/session").get_json()["projects"] is None


def test_missing_projects_are_ignored(studio):
    start, _, first, _, path, _ = studio
    path.write_text(json.dumps({"version": 1, "shown": ["gone", first, first]}))
    _, client, _ = start()
    assert client.get("/api/studio/session").get_json()["projects"] == [first]


@pytest.mark.parametrize("raw", ["{", "[]", '{"version":1,"shown":"bad"}', '{"version":9,"shown":[]}'])
def test_invalid_state_uses_startup_defaults(studio, raw):
    start, _, _, _, path, _ = studio
    path.write_text(raw)
    _, client, _ = start()
    assert client.get("/api/studio/session").get_json()["projects"] is None


def test_connect_and_forget_update_saved_selection(studio, tmp_path):
    start, _, first, second, path, _ = studio
    _, client, headers = start(STUDIO_PROJECTS=[first])
    folder = tmp_path / "connected"
    folder.mkdir()
    connected = client.post("/api/projects/connect", json={"path": str(folder)}, headers=headers).get_json()["identifier"]
    assert json.loads(path.read_text())["shown"] == [first, connected]
    client.post(f"/api/studio/projects/{first}/forget", headers=headers)
    _, client, _ = start()
    assert client.get("/api/studio/session").get_json()["projects"] == [connected]


def test_unwritable_state_does_not_break_visibility(studio, monkeypatch):
    start, _, first, second, _, _ = studio
    _, client, headers = start()

    def fail(*args, **kwargs):
        raise PermissionError("blocked")

    monkeypatch.setattr("alfrd.gui.project_selection.os.replace", fail)
    response = client.post(f"/api/studio/projects/{second}/visibility", json={"state": "hidden"}, headers=headers)
    assert response.status_code == 200
    assert response.get_json()["projects"] == [first]
