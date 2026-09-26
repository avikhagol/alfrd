"""Integration regressions found during Astra's review of the GUI lanes."""
from pathlib import Path

import pytest

from alfrd.gui import create_app
from alfrd.gui.services import RuntimeCatalogReader, resolve_artifact_path, resolve_selected_manifest
from alfrd.gui.summaries import build_summaries
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
    response = client.post("/dashboard/connect", data={"path": str(root), "csrf_token": "known"})
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


def test_missing_artifact_run_root_is_a_validation_error(tmp_path):
    with pytest.raises(ValueError):
        resolve_artifact_path(tmp_path / "removed-run", "report.txt")


def test_artifact_symlink_cannot_alias_a_hidden_target(tmp_path):
    hidden = tmp_path / ".private"
    hidden.write_text("synthetic test data, not a credential")
    (tmp_path / "report.txt").symlink_to(hidden)
    with pytest.raises(ValueError):
        resolve_artifact_path(tmp_path, "report.txt")


def test_non_ascii_csrf_token_is_rejected_not_a_500(runtime_app):
    app, _ = runtime_app
    client = app.test_client()
    with client.session_transaction() as session:
        session["_alfrd_csrf_token"] = "known"
    response = client.post("/dashboard/connect", data={"csrf_token": "\u2603", "path": "/tmp"})
    assert response.status_code == 403


def test_result_summary_uses_latest_run_per_workflow_and_dataset(runtime_app, tmp_path):
    _, service = runtime_app
    root = tmp_path / "project"
    root.mkdir()
    (root / "alfrd.yaml").write_text("name: demo\nsummaries:\n  result: {}\n")
    project = service.create_project("demo", root)
    workflow = service.create_workflow(project.id, "reduce", [{"key": "one", "command": ["never-run"]}])
    dataset = service.create_dataset(project.id, "sample")
    for status in ("failed", "succeeded"):
        service.import_historical_run(workflow.id, dataset.id, working_directory=root, status=status,
                                      executions=[{"key": "one", "status": status}])
    result = build_summaries({"id": project.id, "root_path": str(root)}, service)["result"]
    assert [row["status"] for row in result["rows"]] == ["succeeded"]


def test_rendered_connect_error_and_read_only_forms(runtime_app):
    app, _ = runtime_app
    client = app.test_client()
    response = client.get("/dashboard/connect")
    assert response.status_code == 200
    with client.session_transaction() as session:
        token = session["_alfrd_csrf_token"]
    response = client.post("/dashboard/connect", data={"csrf_token": token, "path": "/missing-gui-project"})
    assert response.status_code == 400
    assert b'role="alert"' in response.data
    assert b'value="/missing-gui-project"' in response.data
    response = client.get("/dashboard/connect", environ_base={"REMOTE_ADDR": "203.0.113.10"})
    assert b'type="submit"' not in response.data


def test_summary_rendering_escapes_snapshot_and_survives_malformed_manifest(runtime_app, tmp_path):
    app, service = runtime_app
    root = tmp_path / "summary"
    root.mkdir()
    manifest = root / ".alfrd.yaml"
    manifest.write_text("name: summary\nsummaries:\n  config:\n    rows:\n      - {step: reduce, parameter: note, value: '<script>alert(1)</script>'}\n")
    service.register_manifest(load_manifest(manifest), create_root=False)
    client = app.test_client()
    response = client.get("/dashboard/project/summary")
    assert response.status_code == 200
    assert b"&lt;script&gt;alert(1)&lt;/script&gt;" in response.data
    assert b"<script>alert(1)</script>" not in response.data
    manifest.write_bytes(b"name: invalid\xff")
    response = client.get("/dashboard/project/summary")
    assert response.status_code == 200
    assert b"Could not read YAML manifest" in response.data
