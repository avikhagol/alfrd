"""Integration regressions found during Astra's review of the GUI lanes."""
from pathlib import Path

import pytest

from alfrd.gui import create_app
from alfrd.gui.services import RuntimeCatalogReader, resolve_selected_manifest
from alfrd.manifest import ManifestError, load_manifest
from alfrd.runtime import RuntimeService, RuntimeStore


@pytest.fixture
def runtime_app(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    service = RuntimeService(store)
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite://",
        "RUNTIME_SERVICE": service,
        "CATALOG_READER": RuntimeCatalogReader(service),
    })
    return app, service


def test_invalid_utf8_manifest_is_a_validation_error(tmp_path):
    manifest = tmp_path / "alfrd.yaml"
    manifest.write_bytes(b"name: invalid\xff")
    with pytest.raises(ManifestError):
        load_manifest(manifest)


def test_failed_connection_rolls_back_all_runtime_rows(runtime_app, tmp_path, monkeypatch):
    app, service = runtime_app
    root = tmp_path / "atomic"
    root.mkdir()
    (root / "alfrd.yaml").write_text("name: atomic\nentrypoint:\n  - {name: one, cmd: [never-run]}\n")
    original = service._audit_entity

    def fail_workflow(session, kind, *args, **kwargs):
        if kind == "workflow_definition":
            raise ValueError("simulated persistence failure")
        return original(session, kind, *args, **kwargs)

    monkeypatch.setattr(service, "_audit_entity", fail_workflow)
    client = app.test_client()
    with client.session_transaction() as session:
        session["_alfrd_csrf_token"] = "known"
    response = client.post("/api/projects/connect", json={"path": str(root)}, headers={"X-CSRF-Token": "known"})
    assert response.status_code == 409
    assert service.list_projects() == []


def test_selected_directory_cannot_follow_external_manifest_symlink(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.yaml"
    outside.write_text("name: outside\n")
    (root / "alfrd.yaml").symlink_to(outside)
    with pytest.raises(ValueError):
        resolve_selected_manifest(root)


def test_non_ascii_csrf_token_is_rejected_not_a_500(runtime_app):
    app, _ = runtime_app
    client = app.test_client()
    with client.session_transaction() as session:
        session["_alfrd_csrf_token"] = "known"
    response = client.post("/api/projects/connect", data={"csrf_token": "\u2603", "path": "/tmp"})
    assert response.status_code == 403
