from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from alfrd.runtime import (
    InvalidTransition,
    LocalSubprocessWorker,
    RuntimeService,
    RuntimeStore,
    RuntimeNotFound,
    Status,
    run_workflow,
)
from alfrd.runtime.service import DuplicateRunError, ParameterValidationError


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

    assert store.schema_version == 3
    assert run.status == Status.PENDING.value
    assert [step.step_definition.key for step in run.step_executions] == ["prepare", "finish"]
    manifest = json.loads((Path(run.working_directory) / ".alfrd" / "run.json").read_text())
    assert manifest["run_id"] == run.id
    assert manifest["dataset_id"] == dataset.id
    assert manifest["steps"][0]["status"] == "pending"


def test_projects_with_same_manifest_name_use_location_identifiers(runtime, tmp_path):
    _, service = runtime
    first = service.create_project("shared", tmp_path / "one")
    second = service.create_project("shared", tmp_path / "two")

    assert first.identifier != second.identifier
    assert service.get_project_by_identifier(first.identifier).root_path == first.root_path
    assert service.get_project_by_selector(second.identifier).root_path == second.root_path
    with pytest.raises(RuntimeNotFound, match="ambiguous"):
        service.get_project_by_name("shared")


def test_v1_database_migrates_project_identity(tmp_path):
    database = tmp_path / "legacy.sqlite"
    root = tmp_path / "legacy-project"
    root.mkdir()
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE alfrd_schema_versions (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
            INSERT INTO alfrd_schema_versions VALUES (1, CURRENT_TIMESTAMP);
            CREATE TABLE runtime_projects (
              id VARCHAR(36) PRIMARY KEY, name VARCHAR(255) NOT NULL UNIQUE,
              root_path TEXT NOT NULL, description TEXT,
              created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
            );
            CREATE TABLE workflow_definitions (
              id VARCHAR(36) PRIMARY KEY,
              project_id VARCHAR(36) NOT NULL REFERENCES runtime_projects(id),
              name VARCHAR(255) NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO runtime_projects VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            ("legacy-id", "legacy", str(root), None),
        )
        connection.execute(
            "INSERT INTO runtime_projects VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            ("renamed-id", "renamed", str(root), None),
        )
        connection.execute(
            "INSERT INTO workflow_definitions VALUES (?, ?, ?)",
            ("workflow-id", "legacy-id", "run"),
        )

    store = RuntimeStore(database)
    store.initialize()
    project = RuntimeService(store).get_project_by_name("legacy")
    assert store.schema_version == 3
    assert project.identifier.endswith(".legacy-project.legacy")
    renamed = RuntimeService(store).get_project_by_name("renamed")
    assert renamed.identifier.endswith(".legacy-project.renamed")
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_v2_path_identifier_gains_manifest_name(tmp_path):
    database = tmp_path / "v2.sqlite"
    root = tmp_path / "project"
    root.mkdir()
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE alfrd_schema_versions (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
            INSERT INTO alfrd_schema_versions VALUES (2, CURRENT_TIMESTAMP);
            CREATE TABLE runtime_projects (
              id VARCHAR(36) PRIMARY KEY, identifier TEXT NOT NULL UNIQUE,
              name VARCHAR(255) NOT NULL, root_path TEXT NOT NULL,
              description TEXT, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO runtime_projects VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            ("project-id", "host.old.path", "science", str(root), None),
        )

    store = RuntimeStore(database)
    store.initialize()
    project = RuntimeService(store).get_project_by_name("science")
    assert store.schema_version == 3
    assert project.identifier.endswith(".project.science")


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
    project = service.create_project(
        "demo",
        tmp_path / "demo",
    )
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


def test_local_worker_writes_live_log_and_clears_pid_on_finish(runtime, tmp_path):
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

    assert result.log_path is not None
    assert Path(result.log_path).is_file()
    assert Path(result.log_path).read_text().strip() == "worker-output"
    logs = service.step_logs(execution.id)
    assert logs["content"].strip() == "worker-output"
    # pid was attached while running, and cleared once the run finished.
    service.transition_run(run.id, Status.SUCCEEDED)
    assert service.get_run(run.id).pid is None


def test_start_run_validates_parameters_before_enqueue(runtime, tmp_path):
    store, service = runtime
    project = service.create_project("demo", tmp_path / "demo")
    workflow = service.create_workflow(project.id, "wf", [{"key": "one", "command": ["true"]}])
    dataset = service.create_dataset(project.id, "a")

    with pytest.raises(ParameterValidationError):
        service.start_run(workflow.id, dataset.id, parameters={"bad": object()})

    with pytest.raises(ParameterValidationError):
        service.start_run("missing-workflow", dataset.id)

    run = service.start_run(workflow.id, dataset.id, parameters={"good": 1})
    assert run.status == "pending"
    assert [event.action for event in service.audit_events(run.id)] == [
        "created", "start_requested"
    ]


def test_start_run_rejects_duplicate_active_run_for_same_dataset(runtime, tmp_path):
    store, service = runtime
    project = service.create_project("demo", tmp_path / "demo")
    workflow = service.create_workflow(project.id, "wf", [{"key": "one", "command": ["true"]}])
    dataset = service.create_dataset(project.id, "a")

    first = service.start_run(workflow.id, dataset.id)
    with pytest.raises(DuplicateRunError):
        service.start_run(workflow.id, dataset.id)

    service.transition_run(first.id, Status.RUNNING)
    with pytest.raises(DuplicateRunError):
        service.start_run(workflow.id, dataset.id)

    execution = service.next_pending_execution(first.id)
    service.transition_execution(execution.id, Status.RUNNING)
    service.transition_execution(execution.id, Status.SUCCEEDED, exit_code=0)
    service.transition_run(first.id, Status.SUCCEEDED)
    second = service.start_run(workflow.id, dataset.id)
    assert second.id != first.id


def test_cancel_run_requires_active_status_and_is_audited(runtime, tmp_path):
    store, service = runtime
    project = service.create_project("demo", tmp_path / "demo")
    workflow = service.create_workflow(project.id, "wf", [{"key": "one", "command": ["true"]}])
    dataset = service.create_dataset(project.id, "a")
    run = service.create_run(workflow.id, dataset.id)

    cancelled = service.cancel_run(run.id, reason="user requested")
    assert cancelled.status == "cancelled"
    assert cancelled.error == "user requested"
    actions = [event.action for event in service.audit_events(run.id)]
    assert "cancel_requested" in actions
    assert "status_changed" in actions

    with pytest.raises(InvalidTransition):
        service.cancel_run(run.id)


def test_retry_step_requires_earlier_steps_succeeded_and_respects_order(runtime, tmp_path):
    store, service = runtime
    project = service.create_project("demo", tmp_path / "demo")
    workflow = service.create_workflow(
        project.id,
        "wf",
        [
            {"key": "one", "command": ["false"]},
            {"key": "two", "command": ["false"]},
        ],
    )
    dataset = service.create_dataset(project.id, "a")
    run = service.create_run(workflow.id, dataset.id)
    service.transition_run(run.id, Status.RUNNING)
    first = service.next_pending_execution(run.id)
    service.transition_execution(first.id, Status.RUNNING)
    service.transition_execution(first.id, Status.FAILED, exit_code=1, error="boom")
    second = service.next_pending_execution(run.id)
    service.transition_execution(second.id, Status.SKIPPED, error="skipped after failure")
    service.transition_run(run.id, Status.FAILED, error="boom")

    with pytest.raises(InvalidTransition):
        service.retry_step(run.id, "two")

    retried = service.retry_step(run.id, "one")
    assert retried.status == "pending"
    assert retried.attempt == 2
    retried_execution = service.next_pending_execution(retried.id)
    assert retried_execution.step_definition.key == "one"
    assert retried_execution.attempt == 2
    actions = [event.action for event in service.audit_events(run.id)]
    assert "step_retry_requested" in actions


def test_run_logs_prefers_log_path_and_falls_back_to_stdout(runtime, tmp_path):
    store, service = runtime
    project = service.create_project("demo", tmp_path / "demo")
    workflow = service.create_workflow(project.id, "wf", [{"key": "one", "command": ["true"]}])
    dataset = service.create_dataset(project.id, "a")
    run = service.create_run(workflow.id, dataset.id)
    service.transition_run(run.id, Status.RUNNING)
    execution = service.next_pending_execution(run.id)
    service.transition_execution(execution.id, Status.RUNNING)
    service.transition_execution(execution.id, Status.SUCCEEDED, exit_code=0, stdout="from-stdout")

    logs = service.run_logs(run.id)
    assert len(logs) == 1
    assert logs[0]["content"] == "from-stdout"
    assert logs[0]["log_path"] is None


def test_run_workflow_drives_a_pending_run_to_completion(runtime, tmp_path):
    store, service = runtime
    project = service.create_project("demo", tmp_path / "demo")
    workflow = service.create_workflow(
        project.id,
        "wf",
        [
            {"key": "one", "command": [sys.executable, "-c", "print('a')"]},
            {"key": "two", "command": [sys.executable, "-c", "print('b')"]},
        ],
    )
    dataset = service.create_dataset(project.id, "a")
    run = service.start_run(workflow.id, dataset.id)

    final_status = run_workflow(service, run.id)

    assert final_status == Status.SUCCEEDED
    finished = service.get_run(run.id)
    assert finished.status == "succeeded"
    assert [item.status for item in finished.step_executions] == ["succeeded", "succeeded"]


def test_run_workflow_stops_at_first_failed_step(runtime, tmp_path):
    store, service = runtime
    project = service.create_project("demo", tmp_path / "demo")
    workflow = service.create_workflow(
        project.id,
        "wf",
        [
            {"key": "one", "command": ["false"]},
            {"key": "two", "command": ["true"]},
        ],
    )
    dataset = service.create_dataset(project.id, "a")
    run = service.start_run(workflow.id, dataset.id)

    final_status = run_workflow(service, run.id)

    assert final_status == Status.FAILED
    finished = service.get_run(run.id)
    assert finished.status == "failed"
    assert [item.status for item in finished.step_executions] == ["failed", "skipped"]


def test_record_artifact_persists_directory_artifacts_by_stable_digest(runtime, tmp_path):
    store, service = runtime
    project = service.create_project("demo", tmp_path / "demo")
    workflow = service.create_workflow(project.id, "wf", [{"key": "one", "command": ["true"]}])
    dataset = service.create_dataset(project.id, "d1")
    run = service.create_run(workflow.id, dataset.id)
    output_dir = tmp_path / "diagnostics"
    output_dir.mkdir()
    (output_dir / "a.png").write_bytes(b"a")

    artifact = service.record_artifact(
        run.id,
        output_dir,
        media_type=None,
        metadata={"kind": "image_collection"},
    )

    assert artifact.size_bytes == 0
    assert len(artifact.sha256) == 64
    # The digest is a stable function of the resolved path, not file content,
    # since a directory has no single byte stream to hash.
    again = service.record_artifact(run.id, output_dir, metadata={"kind": "image_collection"})
    assert again.sha256 == artifact.sha256


def test_record_artifact_missing_path_raises(runtime, tmp_path):
    store, service = runtime
    project = service.create_project("demo", tmp_path / "demo")
    workflow = service.create_workflow(project.id, "wf", [{"key": "one", "command": ["true"]}])
    dataset = service.create_dataset(project.id, "d1")
    run = service.create_run(workflow.id, dataset.id)

    with pytest.raises(FileNotFoundError):
        service.record_artifact(run.id, tmp_path / "never-created.txt")
