"""Server folder browser → New folder: ``POST /api/studio/fs/mkdir`` and ``writable`` in the listing."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("flask")

from alfrd.gui import create_app  # noqa: E402
from alfrd.gui.services import RuntimeCatalogReader  # noqa: E402
from alfrd.runtime import RuntimeService, RuntimeStore  # noqa: E402

MKDIR = "/api/studio/fs/mkdir"


@pytest.fixture
def studio(tmp_path: Path):
    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    service = RuntimeService(store)
    app = create_app({
        "TESTING": True, "SECRET_KEY": "test-secret",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'catalog.sqlite'}",
        "RUNTIME_SERVICE": service, "CATALOG_READER": RuntimeCatalogReader(service),
        "STUDIO_DEFAULT_PROJECT": None, "STUDIO_DEMO": False,
    })
    client = app.test_client()
    token = client.get("/api/studio/session").get_json()["csrf_token"]
    parent = tmp_path / "data"
    parent.mkdir()
    return app, client, {"X-CSRF-Token": token}, parent


def _error(response) -> dict:
    return response.get_json()["error"]


def test_mkdir_creates_exactly_one_child(studio):
    _, client, h, parent = studio
    response = client.post(MKDIR, json={"parent": str(parent), "name": "analysis"}, headers=h)
    assert response.status_code == 201
    body = response.get_json()
    assert body == {"path": str(parent.resolve() / "analysis"), "name": "analysis", "parent": str(parent.resolve())}
    assert (parent / "analysis").is_dir()
    assert not (parent / "analysis" / "alfrd.yaml").exists()  # creating a folder does not make a project
    # The name is kept as typed (no trimming or other silent changes).
    spaced = client.post(MKDIR, json={"parent": str(parent), "name": " run 1"}, headers=h)
    assert spaced.status_code == 201 and (parent / " run 1").is_dir()


def test_mkdir_rejects_traversal_and_empty_names(studio):
    _, client, h, parent = studio
    for name in ("", "   ", ".", "..", "a/b", "../x", "/abs", "a\\b", "a\0b", None, 3):
        response = client.post(MKDIR, json={"parent": str(parent), "name": name}, headers=h)
        assert response.status_code == 400, name
        assert set(_error(response)) >= {"code", "message"} and _error(response)["code"] == 400
    assert not (parent.parent / "x").exists() and list(parent.iterdir()) == []
    assert client.post(MKDIR, json={"name": "x"}, headers=h).status_code == 400  # no parent
    assert client.post(MKDIR, json=["x"], headers=h).status_code == 400
    assert client.post(MKDIR, data="not json", headers={**h, "Content-Type": "application/json"}).status_code == 400


def test_mkdir_missing_parent_is_404_and_ancestors_are_not_created(studio, tmp_path: Path):
    _, client, h, parent = studio
    missing = tmp_path / "nope" / "deeper"
    response = client.post(MKDIR, json={"parent": str(missing), "name": "x"}, headers=h)
    assert response.status_code == 404 and _error(response)["code"] == 404
    assert not (tmp_path / "nope").exists()
    (parent / "notes.txt").write_text("a file")
    assert client.post(MKDIR, json={"parent": str(parent / "notes.txt"), "name": "x"}, headers=h).status_code == 404


def test_mkdir_existing_folder_or_file_is_409(studio):
    _, client, h, parent = studio
    (parent / "analysis").mkdir()
    (parent / "notes.txt").write_text("keep me")
    folder = client.post(MKDIR, json={"parent": str(parent), "name": "analysis"}, headers=h)
    assert folder.status_code == 409 and "already exists" in _error(folder)["message"]
    file = client.post(MKDIR, json={"parent": str(parent), "name": "notes.txt"}, headers=h)
    assert file.status_code == 409 and (parent / "notes.txt").read_text() == "keep me"


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0, reason="root ignores directory permissions")
def test_mkdir_permission_denied_is_403_with_reason(studio):
    _, client, h, parent = studio
    locked = parent / "locked"
    locked.mkdir()
    locked.chmod(0o555)
    try:
        listing = client.get("/api/studio/fs/list", query_string={"path": str(locked)}, headers=h).get_json()
        assert listing["writable"] is False
        response = client.post(MKDIR, json={"parent": str(locked), "name": "x"}, headers=h)
        assert response.status_code == 403
        assert _error(response)["reason"] == "permission" and _error(response)["code"] == 403
        assert not (locked / "x").exists()
    finally:
        locked.chmod(0o755)


def test_mkdir_needs_csrf_loopback_and_mutations(studio):
    app, client, h, parent = studio
    body = {"parent": str(parent), "name": "x"}
    for response in (
        client.post(MKDIR, json=body),  # no token
        client.post(MKDIR, json=body, headers={"X-CSRF-Token": "wrong"}),
        client.post(MKDIR, json=body, headers={**h, "Origin": "https://evil.example"}),
        client.post(MKDIR, json=body, headers=h, environ_base={"REMOTE_ADDR": "203.0.113.8"}),
    ):
        assert response.status_code == 403
        assert "reason" not in _error(response)  # a session/CSRF refusal, not a filesystem one
    app.config["RUNTIME_MUTATIONS_ENABLED"] = False
    assert client.post(MKDIR, json=body, headers=h).status_code == 403
    assert not (parent / "x").exists()


def test_fs_list_reports_writable(studio):
    _, client, h, parent = studio
    listing = client.get("/api/studio/fs/list", query_string={"path": str(parent)}, headers=h).get_json()
    assert listing["writable"] is True
