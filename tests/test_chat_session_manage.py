"""Chat sessions can be renamed and deleted (chat.rename_session / delete_session, Studio PATCH / DELETE, CLI)."""
from __future__ import annotations

import pytest

from alfrd import chat
from alfrd.runtime import scheduler

from tests.test_chat_orchestration import fake, project, run, say  # noqa: F401  (the project fixture)


def test_rename_trims_and_refuses_empty(project):
    sid = chat.create_session(project, "First")["id"]
    assert chat.rename_session(project, sid, "  Release   notes \n")["title"] == "Release notes"
    assert chat.rename_session(project, sid, "x" * 300)["title"] == "x" * chat.TITLE_MAX
    with pytest.raises(ValueError):
        chat.rename_session(project, sid, "   ")
    with pytest.raises(LookupError):
        chat.rename_session(project, "20260101-000000-abcdef", "nope")
    assert [s["title"] for s in chat.list_sessions(project)] == ["x" * chat.TITLE_MAX]


def test_delete_removes_session_and_plan_folders(project):
    keep = chat.create_session(project, "Keep")["id"]
    sid = chat.create_session(project, "Drop")["id"]
    fake(project, t1={"text": "done"})
    say(project, sid, "@codex do it")
    run(project, sid)
    store = chat.session(project, sid)
    plan = scheduler.PlanDir(store.root, store.plan_id).path
    assert store.dir.is_dir() and plan.is_dir()
    assert chat.delete_session(project, sid)["deleted"] is True
    assert not store.dir.exists() and not plan.exists()
    assert [s["id"] for s in chat.list_sessions(project)] == [keep]
    with pytest.raises(LookupError):
        chat.delete_session(project, sid)


def test_delete_is_refused_while_a_request_is_active(project):
    sid = chat.create_session(project, "Busy")["id"]
    say(project, sid, "@codex do it")  # queued, the runner never started (spawn=False)
    with pytest.raises(PermissionError, match="active requests"):
        chat.delete_session(project, sid)
    assert chat.session(project, sid).dir.is_dir()


def test_studio_rename_and_delete_endpoints(project, tmp_path):
    from alfrd.gui import create_app
    from alfrd.gui.services import RuntimeCatalogReader
    from alfrd.runtime import RuntimeService, RuntimeStore

    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    service = RuntimeService(store)
    service.register_manifest(project / ".alfrd" / "alfrd.yaml")
    ident = service.list_projects()[0].identifier
    app = create_app({"TESTING": True, "SECRET_KEY": "k", "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'c.sqlite'}",
                      "RUNTIME_SERVICE": service, "CATALOG_READER": RuntimeCatalogReader(service),
                      "STUDIO_DEMO": False, "STUDIO_LIVE_INTERVAL": 0})
    client = app.test_client()
    headers = {"X-CSRF-Token": client.get("/api/studio/session").get_json()["csrf_token"]}
    url = f"/api/studio/projects/{ident}/chat/sessions"
    sid = client.post(url, json={"title": "One"}, headers=headers).get_json()["session"]["id"]
    assert client.patch(f"{url}/{sid}", json={"title": "Renamed"}).status_code == 403  # CSRF
    renamed = client.patch(f"{url}/{sid}", json={"title": "Renamed"}, headers=headers)
    assert renamed.status_code == 200 and renamed.get_json()["session"]["title"] == "Renamed"
    assert client.patch(f"{url}/{sid}", json={"title": ""}, headers=headers).status_code == 400
    say(project, sid, "@codex busy")
    assert client.delete(f"{url}/{sid}", headers=headers).status_code == 409
    fake(project, t1={"text": "done"})
    run(project, sid)
    assert client.delete(f"{url}/{sid}", headers=headers).status_code == 200
    assert client.get(url).get_json()["sessions"] == []
    assert client.delete(f"{url}/{sid}", headers=headers).status_code == 404
