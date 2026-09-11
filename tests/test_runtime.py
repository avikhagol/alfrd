from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from alfrd.runtime import (
    InvalidTransition,
    LocalSubprocessWorker,
    RuntimeService,
    RuntimeStore,
    Status,
)


@pytest.fixture
def runtime(tmp_path: Path) -> tuple[RuntimeStore, RuntimeService]:
    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    return store, RuntimeService(store)


def test_schema_and_complete_object_graph(runtime, tmp_path):
    store, service = runtime
    project = service.create_project("demo", tmp_path / "demo")
    workflow = service.create_workflow(
        project.id,
        "reduce",
        [
            {"key": "prepare", "command": [sys.executable, "-c", "print('ok')"]},
            {"key": "finish", "command": [sys.executable, "-c", "print('done')"]},
        ],
    )
    dataset = service.create_dataset(project.id, "observation-1", uri="file:///input")
    run = service.create_run(workflow.id, dataset.id)

    assert store.schema_version == 1
    assert run.status == Status.PENDING.value
    assert [step.step_definition.key for step in run.step_executions] == ["prepare", "finish"]
    manifest = json.loads((Path(run.working_directory) / ".alfrd" / "run.json").read_text())
    assert manifest["run_id"] == run.id
    assert manifest["dataset_id"] == dataset.id
    assert manifest["steps"][0]["status"] == "pending"


def test_transitions_audit_artifact_and_manifest(runtime, tmp_path):
    store, service = runtime
    project = service.create_project("demo", tmp_path / "demo")
    workflow = service.create_workflow(project.id, "wf", [{"key": "one", "command": ["true"]}])
    dataset = service.create_dataset(project.id, "d1")
    run = service.create_run(workflow.id, dataset.id)

    service.transition_run(run.id, Status.RUNNING)
    execution = service.next_pending_execution(run.id)
    service.transition_execution(execution.id, Status.RUNNING)
    output = tmp_path / "product.txt"
    output.write_text("payload")
    artifact = service.record_artifact(
        run.id,
        output,
        step_execution_id=execution.id,
        media_type="text/plain",
        metadata={"kind": "result"},
    )
    service.transition_execution(execution.id, Status.SUCCEEDED, exit_code=0)
    finished = service.transition_run(run.id, Status.SUCCEEDED)

    assert artifact.size_bytes == 7
    assert len(artifact.sha256) == 64
    assert artifact.metadata_json == {"kind": "result"}
    assert finished.finished_at is not None
    assert [event.action for event in service.audit_events(run.id)] == [
        "created", "status_changed", "status_changed", "artifact_recorded",
        "status_changed", "status_changed",
    ]
    with pytest.raises(InvalidTransition):
        service.transition_run(run.id, Status.RUNNING)
    manifest = service.load_manifest(Path(run.working_directory))
    assert manifest["status"] == "succeeded"
    assert manifest["artifacts"][0]["sha256"] == artifact.sha256


def test_stale_recovery_resume_retry_and_manifest_recovery(runtime, tmp_path):
    store, service = runtime
    project = service.create_project("demo", tmp_path / "demo")
    workflow = service.create_workflow(project.id, "wf", [{"key": "one", "command": ["true"]}])
    dataset = service.create_dataset(project.id, "d1")
    run = service.create_run(workflow.id, dataset.id)
    service.transition_run(run.id, Status.RUNNING)
    execution = service.next_pending_execution(run.id)
    service.transition_execution(execution.id, Status.RUNNING)
    with store.session() as session:
        db_run = session.get(type(run), run.id)
        db_run.heartbeat_at = datetime.now(timezone.utc) - timedelta(hours=2)

    recovered = service.recover_stale(timedelta(minutes=5))
    assert [item.id for item in recovered] == [run.id]
    assert service.get_run(run.id).status == "interrupted"
    resumed = service.resume_run(run.id)
    assert resumed.id == run.id
    assert resumed.status == "pending"
    assert service.next_pending_execution(run.id).attempt == 2
    service.transition_run(run.id, Status.RUNNING)
    resumed_execution = service.next_pending_execution(run.id)
    service.transition_execution(resumed_execution.id, Status.RUNNING)
    service.transition_execution(resumed_execution.id, Status.SUCCEEDED, exit_code=0)
    service.transition_run(run.id, Status.SUCCEEDED)
    with pytest.raises(InvalidTransition):
        service.retry_run(run.id)

    failed = service.create_run(workflow.id, dataset.id)
    service.transition_run(failed.id, Status.RUNNING)
    service.transition_run(failed.id, Status.FAILED, error="boom")
    retry = service.retry_run(failed.id)
    assert retry.id != run.id
    assert retry.parent_run_id == failed.id
    assert retry.dataset_id == dataset.id

    with store.session() as session:
        session.delete(session.get(type(retry), retry.id))
    restored = service.recover_manifest(Path(retry.working_directory) / ".alfrd" / "run.json")
    assert restored.id == retry.id
    assert restored.dataset_id == dataset.id
    assert restored.working_directory == retry.working_directory


def test_dataset_csv_round_trip_keeps_datasets_independent(runtime, tmp_path):
    store, service = runtime
    project = service.create_project("demo", tmp_path / "demo")
    service.create_dataset(project.id, "a", name="Alpha", metadata={"band": "X"})
    service.create_dataset(project.id, "b", name="Beta", uri="file:///b")
    csv_path = tmp_path / "datasets.csv"
    service.export_datasets_csv(project.id, csv_path)

    other = service.create_project("other", tmp_path / "other")
    imported = service.import_datasets_csv(other.id, csv_path)
    assert [item.external_id for item in imported] == ["a", "b"]
    assert imported[0].metadata_json == {"band": "X"}
    assert all(item.project_id == other.id for item in imported)
    assert len(service.list_datasets(project.id)) == 2


def test_local_worker_executes_only_claimed_step(runtime, tmp_path):
    store, service = runtime
    project = service.create_project("demo", tmp_path / "demo")
    workflow = service.create_workflow(
        project.id,
        "wf",
        [{"key": "one", "command": [sys.executable, "-c", "print('worker-output')"]}],
    )
    dataset = service.create_dataset(project.id, "a")
    run = service.create_run(workflow.id, dataset.id)
    service.transition_run(run.id, Status.RUNNING)
    execution = service.next_pending_execution(run.id)

    result = LocalSubprocessWorker(service).execute(execution.id)

    assert result.status == "succeeded"
    assert result.exit_code == 0
    assert result.stdout.strip() == "worker-output"
    assert service.get_run(run.id).status == "running"  # orchestration remains external