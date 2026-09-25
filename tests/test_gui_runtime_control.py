from __future__ import annotations

import sys
from pathlib import Path

import pytest

from alfrd.gui import create_app
from alfrd.runtime import RuntimeService, RuntimeStore, Status


class RecordingSpawner:
    """Stand-in for subprocess.Popen that records calls without spawning."""

    def __init__(self):
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        return None


@pytest.fixture()
def runtime_service(tmp_path: Path) -> RuntimeService:
    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    return RuntimeService(store)


@pytest.fixture()
def seeded(runtime_service: RuntimeService, tmp_path: Path):
    project = runtime_service.create_project("demo", tmp_path / "demo")
    workflow = runtime_service.create_workflow(
        project.id,
        "wf",
        [{"key": "one", "command": [sys.executable, "-c", "print('ok')"]}],
    )
    dataset = runtime_service.create_dataset(project.id, "a")
    return project, workflow, dataset


@pytest.fixture()
def spawner() -> RecordingSpawner:
    return RecordingSpawner()


@pytest.fixture()
def client(runtime_service, spawner, tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'catalog.sqlite'}",
            "RUNTIME_SERVICE": runtime_service,
            "RUNTIME_SPAWN": spawner,
        }
    )
    client = app.test_client()
    with client.session_transaction() as session:
        session["_alfrd_csrf_token"] = "test-token"
    original_post = client.post

    def csrf_post(*args, **kwargs):
        headers = kwargs.setdefault("headers", {})
        headers.setdefault("X-CSRF-Token", "test-token")
        return original_post(*args, **kwargs)

    client.post = csrf_post
    return client


def test_control_routes_are_503_without_a_configured_runtime_service():
    app = create_app({"TESTING": True})
    client = app.test_client()

    response = client.post("/api/runtime/runs", json={"workflow_id": "x", "dataset_id": "y"})

    assert response.status_code == 403


def test_start_run_never_executes_synchronously_and_spawns_worker(client, seeded, spawner):
    project, workflow, dataset = seeded

    response = client.post(
        "/api/runtime/runs",
        json={"workflow_id": workflow.id, "dataset_id": dataset.id},
    )

    assert response.status_code == 201, response.get_json()
    body = response.get_json()
    assert len(body["started"]) == 1
    run = body["started"][0]
    assert run["status"] == "pending"
    # The worker must be spawned as a separate process, not executed inline:
    # the run stays pending/not actually driven by the request itself.
    assert len(spawner.calls) == 1
    assert spawner.calls[0][0][:3] == [sys.executable, "-m", "alfrd.cli"]
    assert "execute" in spawner.calls[0][0]


def test_start_run_rejects_invalid_parameters_before_enqueue(client, seeded):
    project, workflow, dataset = seeded

    response = client.post(
        "/api/runtime/runs",
        json={
            "workflow_id": "missing-workflow",
            "dataset_id": dataset.id,
        },
    )

    assert response.status_code == 400
    body = response.get_json()
    assert body["errors"]
    assert body["started"] == []


def test_start_run_rejects_duplicate_concurrent_run_for_same_dataset(client, seeded, spawner):
    project, workflow, dataset = seeded

    first = client.post(
        "/api/runtime/runs", json={"workflow_id": workflow.id, "dataset_id": dataset.id}
    )
    assert first.status_code == 201

    second = client.post(
        "/api/runtime/runs", json={"workflow_id": workflow.id, "dataset_id": dataset.id}
    )

    assert second.status_code == 400
    assert second.get_json()["errors"]
    assert len(spawner.calls) == 1


def test_start_run_for_all_eligible_datasets_via_dataset_ids(client, seeded, spawner, runtime_service):
    project, workflow, dataset = seeded
    other = runtime_service.create_dataset(project.id, "b")

    response = client.post(
        "/api/runtime/runs",
        json={"workflow_id": workflow.id, "dataset_ids": [dataset.id, other.id]},
    )

    assert response.status_code == 201
    assert len(response.get_json()["started"]) == 2
    assert len(spawner.calls) == 2


def test_get_run_logs_and_audit_endpoints(client, seeded, runtime_service):
    project, workflow, dataset = seeded
    run = runtime_service.create_run(workflow.id, dataset.id)
    runtime_service.transition_run(run.id, Status.RUNNING)
    execution = runtime_service.next_pending_execution(run.id)
    runtime_service.transition_execution(execution.id, Status.RUNNING)
    runtime_service.transition_execution(execution.id, Status.SUCCEEDED, exit_code=0, stdout="hi")

    logs = client.get(f"/api/runtime/runs/{run.id}/logs")
    assert logs.status_code == 200
    assert logs.get_json()["logs"][0]["content"] == "hi"

    audit = client.get(f"/api/runtime/runs/{run.id}/audit")
    assert audit.status_code == 200
    actions = [event["action"] for event in audit.get_json()["events"]]
    assert "created" in actions
    assert "status_changed" in actions

    missing = client.get("/api/runtime/runs/does-not-exist/logs")
    assert missing.status_code == 404


def test_resume_and_retry_endpoints_spawn_a_worker(client, seeded, spawner, runtime_service):
    project, workflow, dataset = seeded
    run = runtime_service.create_run(workflow.id, dataset.id)
    runtime_service.transition_run(run.id, Status.RUNNING)
    runtime_service.transition_run(run.id, Status.FAILED, error="boom")
    spawner.calls.clear()

    resumed = client.post(f"/api/runtime/runs/{run.id}/resume")
    assert resumed.status_code == 200
    assert resumed.get_json()["status"] == "pending"
    assert len(spawner.calls) == 1

    other_run = runtime_service.create_run(workflow.id, dataset.id)
    runtime_service.transition_run(other_run.id, Status.RUNNING)
    runtime_service.transition_run(other_run.id, Status.FAILED, error="boom")

    retried = client.post(f"/api/runtime/runs/{other_run.id}/retry")
    assert retried.status_code == 200
    assert retried.get_json()["id"] != other_run.id
    assert len(spawner.calls) == 2

    conflict = client.post("/api/runtime/runs/does-not-exist/resume")
    assert conflict.status_code == 404


def test_retry_step_endpoint_respects_order_and_spawns_a_worker(
    client, seeded, spawner, runtime_service
):
    project, workflow, dataset = seeded
    run = runtime_service.create_run(workflow.id, dataset.id)
    runtime_service.transition_run(run.id, Status.RUNNING)
    execution = runtime_service.next_pending_execution(run.id)
    runtime_service.transition_execution(execution.id, Status.RUNNING)
    runtime_service.transition_execution(execution.id, Status.FAILED, exit_code=1, error="boom")
    runtime_service.transition_run(run.id, Status.FAILED, error="boom")
    spawner.calls.clear()

    response = client.post(f"/api/runtime/runs/{run.id}/steps/one/retry")

    assert response.status_code == 200
    assert response.get_json()["status"] == "pending"
    assert len(spawner.calls) == 1

    unknown_step = client.post(f"/api/runtime/runs/{run.id}/steps/missing/retry")
    assert unknown_step.status_code == 409


def test_cancel_run_requires_explicit_confirmation(client, seeded, runtime_service):
    project, workflow, dataset = seeded
    run = runtime_service.create_run(workflow.id, dataset.id)

    unconfirmed = client.post(f"/api/runtime/runs/{run.id}/cancel", json={})
    assert unconfirmed.status_code == 400
    assert runtime_service.get_run(run.id).status == "pending"

    confirmed = client.post(
        f"/api/runtime/runs/{run.id}/cancel", json={"confirm": True, "reason": "no longer needed"}
    )
    assert confirmed.status_code == 200
    assert confirmed.get_json()["status"] == "cancelled"

    already_terminal = client.post(
        f"/api/runtime/runs/{run.id}/cancel", json={"confirm": True}
    )
    assert already_terminal.status_code == 409


def test_every_control_action_is_recorded_in_the_audit_trail(
    client, seeded, runtime_service
):
    project, workflow, dataset = seeded

    response = client.post(
        "/api/runtime/runs", json={"workflow_id": workflow.id, "dataset_id": dataset.id}
    )
    run_id = response.get_json()["started"][0]["id"]

    client.post(f"/api/runtime/runs/{run_id}/cancel", json={"confirm": True})

    actions = [event.action for event in runtime_service.audit_events(run_id)]
    assert actions == ["created", "start_requested", "cancel_requested", "status_changed"]
