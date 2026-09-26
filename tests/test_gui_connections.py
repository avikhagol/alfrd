from __future__ import annotations

from pathlib import Path

import pytest

from alfrd.gui import create_app
from alfrd.gui.services import RuntimeCatalogReader
from alfrd.runtime import RuntimeService, RuntimeStore


@pytest.fixture
def connected_app(tmp_path: Path):
    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    service = RuntimeService(store)
    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'catalog.sqlite'}",
            "RUNTIME_SERVICE": service,
            "CATALOG_READER": RuntimeCatalogReader(service),
        }
    )
    return app, service


def _csrf(client) -> dict[str, str]:
    with client.session_transaction() as session:
        session["_alfrd_csrf_token"] = "known-token"
    return {"X-CSRF-Token": "known-token"}


def test_connect_exact_directory_alias_precedence_and_persistence(connected_app, tmp_path):
    app, service = connected_app
    root = tmp_path / "consumer"
    root.mkdir()
    legacy = root / "alfrd.yaml"
    alias = root / ".alfrd.yaml"
    legacy.write_text("name: preferred\n", encoding="utf-8")
    alias.write_text("name: ignored\n", encoding="utf-8")
    before = legacy.read_bytes()

    client = app.test_client()
    response = client.post(
        "/dashboard/connect", data={"path": str(root)}, headers=_csrf(client)
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith(
        f"/dashboard/project/{service.get_project_by_name('preferred').identifier}"
    )
    assert service.get_project_by_name("preferred").root_path == str(root.resolve())
    assert legacy.read_bytes() == before

    duplicate = client.post(
        "/dashboard/connect", data={"path": str(alias)}, headers=_csrf(client)
    )
    assert duplicate.status_code == 302


def test_connect_accepts_alias_when_legacy_name_is_absent(connected_app, tmp_path):
    app, service = connected_app
    root = tmp_path / "alias-only"
    root.mkdir()
    alias = root / ".alfrd.yaml"
    alias.write_text("name: alias-only\n", encoding="utf-8")
    client = app.test_client()

    response = client.post(
        "/dashboard/connect", data={"path": str(alias)}, headers=_csrf(client)
    )

    assert response.status_code == 302
    assert service.get_project_by_name("alias-only").root_path == str(root.resolve())
    assert [item.name for item in root.iterdir()] == [".alfrd.yaml"]


def test_connect_rejects_ancestor_walk_and_unsafe_names_but_allows_name_duplicates(
    connected_app, tmp_path, monkeypatch
):
    app, service = connected_app
    root = tmp_path / "root"
    nested = root / "nested"
    nested.mkdir(parents=True)
    (root / "alfrd.yaml").write_text("name: parent\n", encoding="utf-8")
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "alfrd.yaml").write_text("name: ../unsafe\n", encoding="utf-8")
    other = tmp_path / "other"
    other.mkdir()
    (other / "alfrd.yaml").write_text("name: duplicate\n", encoding="utf-8")
    service.create_project("duplicate", tmp_path / "existing")
    monkeypatch.setattr("alfrd.gui.routes.render_template", lambda *a, **kw: kw)
    client = app.test_client()
    headers = _csrf(client)

    assert client.post("/dashboard/connect", data={"path": str(nested)}, headers=headers).status_code == 400
    assert client.post("/dashboard/connect", data={"path": str(bad)}, headers=headers).status_code == 400
    assert client.post("/dashboard/connect", data={"path": str(other)}, headers=headers).status_code == 302
    duplicates = [p for p in client.get("/api/projects").get_json()["projects"] if p["name"] == "duplicate"]
    assert {p["display_name"] for p in duplicates} == {"duplicate (existing)", "duplicate (other)"}
    assert len({p["identifier"] for p in duplicates}) == 2


def test_mutations_require_csrf_and_are_local_only(connected_app, tmp_path):
    app, _ = connected_app
    root = tmp_path / "consumer"
    root.mkdir()
    (root / "alfrd.yaml").write_text("name: safe\n", encoding="utf-8")
    client = app.test_client()

    assert client.post("/dashboard/connect", data={"path": str(root)}).status_code == 403
    assert client.post(
        "/dashboard/connect",
        data={"path": str(root)},
        headers=_csrf(client),
        environ_base={"REMOTE_ADDR": "203.0.113.8"},
    ).status_code == 403
    assert client.post(
        "/dashboard/connect",
        data={"path": str(root)},
        headers={**_csrf(client), "Origin": "https://evil.example"},
    ).status_code == 403


def test_catalog_only_app_remains_read_only(tmp_path, monkeypatch):
    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'catalog.sqlite'}",
        }
    )
    monkeypatch.setattr("alfrd.gui.routes.render_template", lambda *a, **kw: kw)
    client = app.test_client()
    response = client.post("/dashboard/connect", data={"path": str(tmp_path)}, headers=_csrf(client))
    assert response.status_code == 403


def test_dashboard_context_reports_runtime_and_local_mutation_availability(connected_app):
    app, _ = connected_app
    with app.test_request_context("/dashboard/", environ_base={"REMOTE_ADDR": "127.0.0.1"}):
        context = {}
        app.update_template_context(context)
        assert context["runtime_enabled"] is True
        assert context["mutations_enabled"] is True
        assert context["csrf_token"]

    with app.test_request_context("/dashboard/", environ_base={"REMOTE_ADDR": "203.0.113.8"}):
        context = {}
        app.update_template_context(context)
        assert context["runtime_enabled"] is True
        assert context["mutations_enabled"] is False


def test_root_redirects_to_studio(connected_app):
    app, _ = connected_app
    response = app.test_client().get("/")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/studio/")


def test_manifest_sync_detects_changed_entrypoint_command(connected_app, tmp_path):
    _, service = connected_app
    root = tmp_path / "consumer"
    root.mkdir()
    manifest = root / "alfrd.yaml"
    manifest.write_text("name: sync-demo\nentrypoint:\n  - {name: reduce, cmd: [first]}\n")
    project, _ = service.register_manifest(manifest, create_root=False)

    reader = RuntimeCatalogReader(service)
    assert reader.get_manifest(project.id)["sync"]["state"] == "synced"

    manifest.write_text("name: sync-demo\nentrypoint:\n  - {name: reduce, cmd: [second]}\n")
    assert reader.get_manifest(project.id)["sync"]["state"] == "out_of_sync"
