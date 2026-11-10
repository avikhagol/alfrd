"""retry-preview and the expected_status / expected_unit preconditions of retry and cancel (F4, contract C21)."""
from __future__ import annotations

import pytest

from alfrd import chat

from tests.test_chat_orchestration import (configure, fake, plan, project, result, run, say, task)  # noqa: F401


def _failed(project):
    configure(project, final_summary=False)
    fake(project, plan=plan(task("a"), task("b", deps=["a"]), task("c")), a={"exit": 2}, b=result(), c=result())
    sid = chat.create_session(project)["id"]
    say(project, sid, "work")
    run(project, sid)
    return sid


def test_preview_matches_the_rows_retry_requeues(project):
    sid = _failed(project)
    preview = chat.retry_preview(project, sid, "r0001.a")
    assert preview["eligible"] and preview["reason"] is None
    assert preview["current_status"] == "failed" and preview["current_unit"]
    assert preview["affected_rows"] == ["r0001.a", "r0001.b"]  # c is done: not requeued
    fake(project, plan=plan(task("a"), task("b", deps=["a"]), task("c")), a=result(), b=result(), c=result())
    assert chat.retry_task(project, sid, "r0001.a", spawn=False)["requeued"] == preview["affected_rows"]


def test_preview_of_an_ineligible_task(project):
    sid = _failed(project)
    done = chat.retry_preview(project, sid, "r0001.c")
    assert done["eligible"] is False and "done" in done["reason"] and done["affected_rows"] == []
    assert done["current_status"] == "done"
    with pytest.raises(LookupError):
        chat.retry_preview(project, sid, "nope")


def test_stale_preconditions_conflict_and_matching_ones_pass(project):
    sid = _failed(project)
    unit = chat.retry_preview(project, sid, "r0001.a")["current_unit"]
    for kw in ({"expected_status": "running"}, {"expected_unit": "stale-unit"}):
        with pytest.raises(chat.TaskConflict) as err:
            chat.retry_task(project, sid, "r0001.a", spawn=False, **kw)
        assert (err.value.current_status, err.value.current_unit) == ("failed", unit)
    assert chat.retry_preview(project, sid, "r0001.a")["current_status"] == "failed"  # nothing was requeued
    with pytest.raises(chat.TaskConflict):
        chat.cancel_task(project, sid, "r0001.b", spawn=False, expected_status="queued")
    out = chat.retry_task(project, sid, "r0001.a", spawn=False, expected_status="failed", expected_unit=unit)
    assert out["requeued"] == ["r0001.a", "r0001.b"]


def test_http_preview_and_409(project, tmp_path):
    from alfrd.gui import create_app
    from alfrd.gui.services import RuntimeCatalogReader
    from alfrd.runtime import RuntimeService, RuntimeStore

    sid = _failed(project)
    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    service = RuntimeService(store)
    service.register_manifest(project / ".alfrd" / "alfrd.yaml")
    app = create_app({"TESTING": True, "SECRET_KEY": "k", "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'c.sqlite'}",
                      "RUNTIME_SERVICE": service, "CATALOG_READER": RuntimeCatalogReader(service),
                      "STUDIO_DEMO": False, "STUDIO_LIVE_INTERVAL": 0})
    client = app.test_client()
    h = {"X-CSRF-Token": client.get("/api/studio/session").get_json()["csrf_token"]}
    base = f"/api/studio/projects/{service.list_projects()[0].identifier}/chat/sessions/{sid}/tasks"

    got = client.get(f"{base}/r0001.a/retry-preview").get_json()
    assert got["eligible"] and got["affected_rows"] == ["r0001.a", "r0001.b"]
    assert client.get(f"{base}/nope/retry-preview").status_code == 404

    assert client.post(f"{base}/r0001.a/retry", json={"expected_status": "failed"}).status_code == 403  # CSRF gate
    stale = client.post(f"{base}/r0001.a/retry", json={"expected_status": "running", "expected_unit": "x"}, headers=h)
    assert stale.status_code == 409
    body = stale.get_json()
    assert body["error"]["code"] == 409 and body["current_status"] == "failed" and body["current_unit"] == got["current_unit"]
    assert client.post(f"{base}/r0001.b/cancel", json={"expected_unit": "x"}, headers=h).status_code == 409
    ok = client.post(f"{base}/r0001.a/retry", json={"expected_status": "failed", "expected_unit": got["current_unit"]},
                     headers=h)
    assert ok.status_code == 202 and ok.get_json()["requeued"] == ["r0001.a", "r0001.b"]
