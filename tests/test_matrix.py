from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

from alfrd.runtime import RuntimeService, RuntimeStore, Status
from alfrd.runtime.matrix import (
    MATRIX_STATUSES,
    MatrixQueryService,
    export_matrix_csv,
    export_matrix_details_csv,
)


@pytest.fixture
def runtime(tmp_path: Path) -> tuple[RuntimeStore, RuntimeService]:
    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    return store, RuntimeService(store)


def _workflow(service, project_id):
    return service.create_workflow(
        project_id,
        "reduce",
        [
            {"key": "prepare", "command": [sys.executable, "-c", "print('ok')"]},
            {"key": "finish", "command": [sys.executable, "-c", "print('done')"]},
        ],
    )


def test_matrix_reflects_queued_running_and_finished_datasets(runtime, tmp_path):
    store, service = runtime
    project = service.create_project("demo", tmp_path / "demo")
    workflow = _workflow(service, project.id)
    queued = service.create_dataset(project.id, "queued-1")
    active = service.create_dataset(project.id, "active-1")

    run = service.create_run(workflow.id, active.id)
    service.transition_run(run.id, Status.RUNNING)
    execution = service.next_pending_execution(run.id)
    service.transition_execution(execution.id, Status.RUNNING)

    matrix = MatrixQueryService(service).build(project.id, workflow.id)

    assert matrix.steps == ["prepare", "finish"]
    by_dataset = {row.dataset_external_id: row for row in matrix.rows}

    queued_row = by_dataset["queued-1"]
    assert queued_row.run_status == "queued"
    assert queued_row.run_id is None
    assert all(cell.status == "queued" for cell in queued_row.cells.values())

    active_row = by_dataset["active-1"]
    assert active_row.run_status == "running"
    assert active_row.cells["prepare"].status == "running"
    assert active_row.cells["finish"].status == "pending"

    assert matrix.summary.total_datasets == 2
    assert matrix.summary.status_counts["queued"] == 1
    assert matrix.summary.status_counts["running"] == 1


def test_matrix_cell_detail_reports_error_and_artifacts(runtime, tmp_path):
    store, service = runtime
    project = service.create_project("demo", tmp_path / "demo")
    workflow = _workflow(service, project.id)
    dataset = service.create_dataset(project.id, "d1")
    run = service.create_run(workflow.id, dataset.id)
    service.transition_run(run.id, Status.RUNNING)
    execution = service.next_pending_execution(run.id)
    service.transition_execution(execution.id, Status.RUNNING)
    output = tmp_path / "artifact.txt"
    output.write_text("payload")
    service.record_artifact(run.id, output, step_execution_id=execution.id)
    service.transition_execution(
        execution.id, Status.FAILED, exit_code=1, stderr="boom", error="command exited with code 1"
    )
    service.transition_run(run.id, Status.FAILED, error="command exited with code 1")

    matrix = MatrixQueryService(service).build(project.id, workflow.id)
    row = next(r for r in matrix.rows if r.dataset_external_id == "d1")
    cell = row.cells["prepare"]

    assert cell.status == "failed"
    assert cell.error_summary == "command exited with code 1"
    assert cell.artifact_count == 1
    assert cell.started_at is not None
    assert cell.finished_at is not None
    assert cell.duration_seconds is not None

    other = row.cells["finish"]
    assert other.status == "skipped" or other.status == "pending"


def test_matrix_status_filter_and_dataset_search(runtime, tmp_path):
    store, service = runtime
    project = service.create_project("demo", tmp_path / "demo")
    workflow = _workflow(service, project.id)
    service.create_dataset(project.id, "alpha")
    beta = service.create_dataset(project.id, "beta")
    run = service.create_run(workflow.id, beta.id)
    service.transition_run(run.id, Status.RUNNING)
    service.transition_run(run.id, Status.INTERRUPTED)

    filtered = MatrixQueryService(service).build(project.id, workflow.id, status_filter="interrupted")
    assert [row.dataset_external_id for row in filtered.rows] == ["beta"]

    searched = MatrixQueryService(service).build(project.id, workflow.id, search="alp")
    assert [row.dataset_external_id for row in searched.rows] == ["alpha"]

    assert set(MATRIX_STATUSES) == {
        "pending", "queued", "running", "succeeded", "failed", "skipped", "interrupted",
    }


def test_matrix_csv_export_round_trip(runtime, tmp_path):
    store, service = runtime
    project = service.create_project("demo", tmp_path / "demo")
    workflow = _workflow(service, project.id)
    dataset = service.create_dataset(project.id, "d1")
    run = service.create_run(workflow.id, dataset.id)
    service.transition_run(run.id, Status.RUNNING)
    execution = service.next_pending_execution(run.id)
    service.transition_execution(execution.id, Status.RUNNING)
    service.transition_execution(execution.id, Status.SUCCEEDED, exit_code=0)

    matrix = MatrixQueryService(service).build(project.id, workflow.id)

    matrix_csv = tmp_path / "matrix.csv"
    export_matrix_csv(matrix, matrix_csv)
    with matrix_csv.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert rows[0]["dataset_id"] == "d1"
    assert rows[0]["prepare"] == "succeeded"
    assert rows[0]["finish"] == "pending"

    details_csv = tmp_path / "matrix-details.csv"
    export_matrix_details_csv(matrix, details_csv)
    with details_csv.open(newline="", encoding="utf-8") as stream:
        detail_rows = list(csv.DictReader(stream))
    assert {row["step"] for row in detail_rows if row["dataset_id"] == "d1"} == {"prepare", "finish"}


def test_matrix_import_datasets_csv_then_appears_in_matrix(runtime, tmp_path):
    store, service = runtime
    project = service.create_project("demo", tmp_path / "demo")
    workflow = _workflow(service, project.id)
    csv_path = tmp_path / "datasets.csv"
    csv_path.write_text("external_id,name,uri,metadata\nnew-1,New One,,{}\n", encoding="utf-8")

    service.import_datasets_csv(project.id, csv_path)
    matrix = MatrixQueryService(service).build(project.id, workflow.id)

    assert "new-1" in {row.dataset_external_id for row in matrix.rows}
