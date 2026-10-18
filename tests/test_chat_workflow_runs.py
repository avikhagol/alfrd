"""Chat sessions' plans in the Workflow run views (scheduler.plan_status, Studio plans API, alfrd.api.status).

A chat session is a plan ``chat-<session>`` run by the workflow runner. It is listed apart from workflow runs
(``chat_plans``, ``run_kind: chat``, the session title), never becomes the default or the active workflow run,
and refuses workflow-only edits (added rows, cell edits, retry-failed).
"""
from __future__ import annotations

import pytest

from alfrd import chat
from alfrd.api import status as api
from alfrd.execution import ExecutionError
from alfrd.runtime import scheduler

from test_chat_orchestration import fake, plan, project, result, run, say, task  # noqa: F401  (fixture)


def _session(root, title="Refactor auth"):
    fake(root, plan=plan(task("a"), task("b", owner="sonnet", deps=["a"])), a=result(), b=result())
    sid = chat.create_session(root, title)["id"]
    say(root, sid, "do it")
    run(root, sid)
    return sid


def _workflow_plan(root, pid="20260101-000000-wf", status="finished"):
    folder = scheduler.PlanDir(root, pid)
    folder.save({"id": pid, "created": "2026-01-01T00:00:00", "csv": "alfrd.plan.csv", "status": status, "steps": []})
    return pid


def test_plan_status_lists_chat_runs_apart_and_keeps_the_workflow_default(project):
    sid = _session(project)
    # No workflow run: nothing is shown by default, but the chat run is listed for the picker.
    st = scheduler.plan_status(project)
    assert st["plan"] is None and st["plans"] == []
    assert [(c["id"], c["run_kind"], c["title"]) for c in st["chat_plans"]] == [(f"chat-{sid}", "chat", "Refactor auth")]
    assert st["chat_plans"][0]["chat"] == {"session": sid} and "working" in st["chat_plans"][0]
    wf = _workflow_plan(project)
    st = scheduler.plan_status(project)
    assert st["plan"]["id"] == wf and [p["id"] for p in st["plans"]] == [wf]
    assert [c["id"] for c in st["chat_plans"]] == [f"chat-{sid}"]
    # A running chat session still never becomes the default (working) run.
    folder = scheduler.PlanDir(project, f"chat-{sid}")
    data = folder.load()
    folder.save({**data, "status": "running"})
    assert scheduler.plan_status(project)["plan"]["id"] == wf
    assert scheduler.default_plan_id(project) == wf


def test_plan_status_of_a_chat_run_has_the_task_grid(project):
    sid = _session(project)
    st = scheduler.plan_status(project, f"chat-{sid}")
    assert st["plan"]["id"] == f"chat-{sid}" and st["plan"]["title"] == "Refactor auth"
    assert st["plan"]["run_kind"] == "chat" and st["selected"] == "id"
    assert "error" not in st["table"]
    assert st["table"]["key_column"] == "TASK_ID"
    assert set(st["table"]["steps"]) == set(chat.settings(project)["agents"])
    cells = {r["key"]: {k: v for k, v in r["cells"].items() if v != "skip"} for r in st["table"]["rows"]}
    assert cells["r0001.a"] == {"codex": "done"} and cells["r0001.b"] == {"sonnet": "done"}
    assert len(st["units"]) == 3 and st["agent_totals"]
    assert [c["id"] for c in st["chat_plans"]] == [f"chat-{sid}"]


def test_chat_runs_never_block_workflow_runs(project):
    sid = _session(project)
    folder = scheduler.PlanDir(project, f"chat-{sid}")
    folder.save({**folder.load(), "status": "running"})
    assert scheduler.active_plan(project) is None
    assert scheduler.list_plans(project) == []


def test_pause_resume_cancel_work_on_a_chat_run_but_not_retry_failed(project):
    sid = _session(project)
    pid = f"chat-{sid}"
    folder = scheduler.PlanDir(project, pid)
    folder.save({**folder.load(), "status": "running"})
    assert scheduler.control(project, pid, "pause", spawn=False)["status"] == "paused"
    with pytest.raises(ExecutionError, match="from the chat"):
        scheduler.control(project, pid, "resume", retry_failed=True, spawn=False)
    assert scheduler.control(project, pid, "resume", spawn=False)["status"] == "running"
    assert scheduler.control(project, pid, "cancel", spawn=False)["status"] == "cancelled"


def test_status_api_lists_chat_runs_after_workflow_runs(project):
    sid = _session(project)
    wf = _workflow_plan(project)
    listing = api.list_plans(project)
    assert [(p["id"], p["run_kind"]) for p in listing["plans"]] == [(wf, "workflow"), (f"chat-{sid}", "chat")]
    assert listing["plans"][1]["title"] == "Refactor auth"
    doc = api.plan_status(project, f"chat-{sid}", detail="rows")
    assert doc["plan"]["id"] == f"chat-{sid}" and doc["counts"]["done"] >= 2
    assert {r["row"] for r in doc["rows"]} >= {"r0001.a", "r0001.b"}
    assert api.plan_status(project)["plan"]["id"] == wf  # latest stays a workflow run


def test_studio_plans_api_shows_chat_runs_and_refuses_workflow_edits(project, tmp_path):
    pytest.importorskip("flask")
    from alfrd.gui import create_app
    from alfrd.gui.services import RuntimeCatalogReader
    from alfrd.runtime import RuntimeService, RuntimeStore

    sid = _session(project)
    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    service = RuntimeService(store)
    service.register_manifest(project / ".alfrd" / "alfrd.yaml")
    app = create_app({"TESTING": True, "SECRET_KEY": "k", "RUNTIME_SERVICE": service,
                      "CATALOG_READER": RuntimeCatalogReader(service),
                      "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'catalog.sqlite'}"})
    client = app.test_client()
    head = {"X-CSRF-Token": client.get("/api/studio/session").get_json()["csrf_token"]}
    base = "/api/studio/projects/chatproj/plans"
    st = client.get(base).get_json()
    assert st["plan"] is None and st["chat_plans"][0]["title"] == "Refactor auth"
    st = client.get(f"{base}?id=chat-{sid}").get_json()
    assert st["plan"]["chat"] == {"session": sid} and st["table"]["rows"]
    res = client.post(f"{base}/chat-{sid}/rows", json={"target": "X"}, headers=head)
    assert res.status_code == 400 and "chat" in res.get_json()["error"]["message"]
    res = client.post(f"{base}/chat-{sid}/cells", json={"cells": [{"row": "r0001.a", "step": "codex", "value": "todo"}]}, headers=head)
    assert res.status_code == 400 and "chat" in res.get_json()["error"]["message"]
    res = client.post(f"{base}/chat-{sid}/resume", json={"retry_failed": True}, headers=head)
    assert res.status_code == 404 and "chat" in res.get_json()["error"]["message"]
