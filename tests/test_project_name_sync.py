"""alfrd.yaml `name:` is synced to the runtime row; the identifier stays the stable key (T8)."""
from __future__ import annotations

import pytest

YAML = "version: 1\nname: proj\n"


@pytest.fixture()
def setup(tmp_path):
    from alfrd.manifest_default import register_project_folder
    from alfrd.runtime import RuntimeService, RuntimeStore

    root = tmp_path / "proj"
    root.mkdir()
    (root / "alfrd.yaml").write_text(YAML)
    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    service = RuntimeService(store)
    project, _ = register_project_folder(service, root)
    return root, service, project


def test_renamed_folder_reuses_its_row(setup):
    from alfrd.manifest_default import register_project_folder

    root, service, project = setup
    (root / "alfrd.yaml").write_text("version: 1\nname: renamed\ndescription: new text\n")
    again, _ = register_project_folder(service, root)  # what `alfrd serve` does on restart
    assert again.id == project.id and again.identifier == project.identifier
    assert (again.name, again.description) == ("renamed", "new text")
    assert [p.id for p in service.list_projects()] == [project.id], "no duplicate row"
    assert service.get_project_by_selector("renamed").id == project.id


def test_studio_save_scan_and_connect_sync_the_name(setup):
    pytest.importorskip("flask")
    from alfrd.gui import create_app
    from alfrd.gui.services import RuntimeCatalogReader

    root, service, project = setup
    app = create_app({"TESTING": True, "SECRET_KEY": "k", "SQLALCHEMY_DATABASE_URI": f"sqlite:///{root.parent / 'c.sqlite'}",
                      "RUNTIME_SERVICE": service, "CATALOG_READER": RuntimeCatalogReader(service), "STUDIO_DEMO": False,
                      "STUDIO_LIVE_INTERVAL": 0})
    client = app.test_client()
    h = {"X-CSRF-Token": client.get("/api/studio/session").get_json()["csrf_token"]}
    base = f"/api/studio/projects/{project.identifier}"

    saved = client.post(f"{base}/manifest", json={"text": "version: 1\nname: saved-name\n", "force": True}, headers=h)
    assert saved.status_code == 200 and saved.get_json()["project_name"] == "saved-name"
    assert service.get_project_by_identifier(project.identifier).name == "saved-name"

    (root / "alfrd.yaml").write_text("version: 1\nname: disk-name\n")  # edited on disk, then Re-scan
    assert client.get(f"{base}/scan").get_json()["project_name"] == "disk-name"
    assert service.get_project_by_identifier(project.identifier).name == "disk-name"

    (root / "alfrd.yaml").write_text("version: 1\nname: connected\n")
    connected = client.post("/api/projects/connect", json={"path": str(root)}, headers=h)
    assert connected.status_code == 201, connected.get_json()
    assert connected.get_json()["identifier"] == project.identifier
    assert [(p.id, p.name) for p in service.list_projects()] == [(project.id, "connected")]

    (root / "alfrd.yaml").write_text("version: 1\nname: 'bad name!'\n")  # invalid names are not copied
    assert client.get(f"{base}/scan").get_json()["project_name"] is None
    assert service.get_project_by_identifier(project.identifier).name == "connected"
