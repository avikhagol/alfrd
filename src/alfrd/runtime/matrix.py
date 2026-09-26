"""Read-only dataset x step status matrix built from runtime persistence.

This module defines the query protocol and data used by the sheet-like
monitoring matrix (roadmap workstream ``0.2.7.0``).  It intentionally
performs no mutation: every value here is derived from ``RuntimeService``
records (``Project``/``Run``/``Dataset``/``StepDefinition``/``StepExecution``/
``Artifact``).  A later execution-control lane may start/resume/retry runs
through the same ``RuntimeService``; this module only reads.
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from .models import Run, StepExecution
from .service import RuntimeService, Status

# Public status vocabulary for the matrix.  "queued" and "pending" are both
# exposed because a dataset with no run yet is "queued" (nothing started),
# while a step within a running/created run that has not started is
# "pending".  Both values are part of the roadmap's required status list.
MATRIX_STATUSES: tuple[str, ...] = (
    "pending",
    "queued",
    "running",
    "succeeded",
    "failed",
    "skipped",
    "interrupted",
)

# Statuses persisted by RuntimeService (see Status enum) mapped onto the
# matrix vocabulary.  "cancelled" collapses onto "interrupted" for display
# purposes; nothing in this module renames or mutates persisted statuses.
_RUN_STATUS_DISPLAY: dict[str, str] = {
    Status.PENDING.value: "queued",
    Status.RUNNING.value: "running",
    Status.SUCCEEDED.value: "succeeded",
    Status.FAILED.value: "failed",
    Status.CANCELLED.value: "interrupted",
    Status.INTERRUPTED.value: "interrupted",
    Status.SKIPPED.value: "skipped",
}


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    value = _as_utc(value)
    return value.isoformat() if value is not None else None


def _duration_seconds(started: datetime | None, finished: datetime | None) -> float | None:
    started = _as_utc(started)
    finished = _as_utc(finished)
    if started is None or finished is None:
        return None
    return (finished - started).total_seconds()


@dataclass(frozen=True)
class MatrixCell:
    """One dataset/step intersection: status, timing, and a short summary."""

    step: str
    status: str
    execution_id: str | None = None
    attempt: int = 1
    started_at: str | None = None
    finished_at: str | None = None
    duration_seconds: float | None = None
    exit_code: int | None = None
    result_summary: str | None = None
    error_summary: str | None = None
    artifact_count: int = 0
    artifact_ids: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "status": self.status,
            "execution_id": self.execution_id,
            "attempt": self.attempt,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": self.duration_seconds,
            "exit_code": self.exit_code,
            "result_summary": self.result_summary,
            "error_summary": self.error_summary,
            "artifact_count": self.artifact_count,
            "artifact_ids": list(self.artifact_ids),
        }


@dataclass(frozen=True)
class MatrixRow:
    """One dataset: identity, its latest run (if any), and cells per step."""

    dataset_id: str
    dataset_external_id: str
    dataset_name: str | None
    run_id: str | None
    run_status: str
    run_started_at: str | None
    run_finished_at: str | None
    run_elapsed_seconds: float | None
    cells: dict[str, MatrixCell]

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "dataset_external_id": self.dataset_external_id,
            "dataset_name": self.dataset_name,
            "run_id": self.run_id,
            "run_status": self.run_status,
            "run_started_at": self.run_started_at,
            "run_finished_at": self.run_finished_at,
            "run_elapsed_seconds": self.run_elapsed_seconds,
            "cells": {step: cell.to_dict() for step, cell in self.cells.items()},
        }


@dataclass(frozen=True)
class MatrixSummary:
    """Run summary counts and elapsed time for the current matrix view."""

    total_datasets: int
    status_counts: dict[str, int]
    elapsed_seconds: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_datasets": self.total_datasets,
            "status_counts": dict(self.status_counts),
            "elapsed_seconds": self.elapsed_seconds,
        }


@dataclass(frozen=True)
class Matrix:
    """The full dataset x step matrix for one project/workflow."""

    project_id: str
    workflow_id: str
    steps: list[str]
    rows: list[MatrixRow]
    summary: MatrixSummary

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "workflow_id": self.workflow_id,
            "steps": list(self.steps),
            "rows": [row.to_dict() for row in self.rows],
            "summary": self.summary.to_dict(),
        }

    def find_cell(self, dataset_external_id: str, step: str) -> MatrixCell | None:
        row = next((r for r in self.rows if r.dataset_external_id == dataset_external_id), None)
        if row is None:
            return None
        return row.cells.get(step)


class MatrixReader(Protocol):
    """Narrow read-only query protocol the execution-control lane can reuse.

    A future lane that adds start/resume/retry actions should depend on this
    protocol (or ``RuntimeOperations``) for reads, and add its own separate
    write-side protocol, so read and control concerns never collide.
    """

    def build(
        self,
        project_id: str,
        workflow_id: str,
        *,
        status_filter: str | None = None,
        search: str | None = None,
    ) -> Matrix: ...


def _run_status_display(status: str) -> str:
    return _RUN_STATUS_DISPLAY.get(status, status)


def _latest_executions(run: Run) -> dict[str, StepExecution]:
    latest: dict[str, StepExecution] = {}
    for execution in run.step_executions:
        key = execution.step_definition.key
        current = latest.get(key)
        if current is None or execution.attempt > current.attempt:
            latest[key] = execution
    return latest


def _cell_from_execution(
    step_key: str, execution: StepExecution | None, run_artifacts: Sequence[Any] = ()
) -> MatrixCell:
    if execution is None:
        return MatrixCell(step=step_key, status="pending")
    started = execution.started_at
    finished = execution.finished_at
    result_summary = None
    if execution.status == Status.SUCCEEDED.value:
        result_summary = (execution.stdout or "").strip().splitlines()[-1:] or [""]
        result_summary = result_summary[0] or None
    artifacts = [item for item in run_artifacts if item.step_execution_id == execution.id]
    return MatrixCell(
        step=step_key,
        status=execution.status,
        execution_id=execution.id,
        attempt=execution.attempt,
        started_at=_iso(started),
        finished_at=_iso(finished),
        duration_seconds=_duration_seconds(started, finished),
        exit_code=execution.exit_code,
        result_summary=result_summary,
        error_summary=execution.error,
        artifact_count=len(artifacts),
        artifact_ids=tuple(item.id for item in artifacts),
    )


def _queued_cell(step_key: str) -> MatrixCell:
    return MatrixCell(step=step_key, status="queued")


class MatrixQueryService:
    """Builds :class:`Matrix` snapshots from :class:`RuntimeService` state.

    Read-only by design: it never creates, transitions, or deletes runtime
    records. This is the reference implementation of :class:`MatrixReader`.
    """

    def __init__(self, service: RuntimeService) -> None:
        self.service = service

    def build(
        self,
        project_id: str,
        workflow_id: str,
        *,
        status_filter: str | None = None,
        search: str | None = None,
    ) -> Matrix:
        workflow = self.service.get_workflow(workflow_id)
        if workflow.project_id != project_id:
            raise ValueError("workflow does not belong to project")
        steps = [step.key for step in workflow.steps]

        datasets = self.service.list_datasets(project_id)
        runs = self.service.list_runs(project_id=project_id, dataset_id=None)
        runs_for_workflow = [run for run in runs if run.workflow_id == workflow_id]
        latest_run_by_dataset: dict[str, Run] = {}
        for run in runs_for_workflow:
            current = latest_run_by_dataset.get(run.dataset_id)
            if current is None or run.created_at >= current.created_at:
                latest_run_by_dataset[run.dataset_id] = run

        rows: list[MatrixRow] = []
        for dataset in datasets:
            if search and search.lower() not in dataset.external_id.lower():
                continue
            run = latest_run_by_dataset.get(dataset.id)
            if run is None:
                row_status = "queued"
                cells = {step: _queued_cell(step) for step in steps}
                run_id = None
                run_started_at = None
                run_finished_at = None
                run_elapsed = None
            else:
                row_status = _run_status_display(run.status)
                latest_executions = _latest_executions(run)
                cells = {
                    step: _cell_from_execution(step, latest_executions.get(step), run.artifacts)
                    for step in steps
                }
                run_id = run.id
                run_started_at = _iso(run.started_at)
                run_finished_at = _iso(run.finished_at)
                run_elapsed = _duration_seconds(
                    run.started_at, run.finished_at or datetime.now(timezone.utc)
                )
            if status_filter and row_status != status_filter:
                continue
            rows.append(MatrixRow(
                dataset_id=dataset.id,
                dataset_external_id=dataset.external_id,
                dataset_name=dataset.name,
                run_id=run_id,
                run_status=row_status,
                run_started_at=run_started_at,
                run_finished_at=run_finished_at,
                run_elapsed_seconds=run_elapsed,
                cells=cells,
            ))

        status_counts: dict[str, int] = {status: 0 for status in MATRIX_STATUSES}
        for row in rows:
            status_counts[row.run_status] = status_counts.get(row.run_status, 0) + 1
        elapsed_values = [row.run_elapsed_seconds for row in rows if row.run_elapsed_seconds is not None]
        summary = MatrixSummary(
            total_datasets=len(rows),
            status_counts=status_counts,
            elapsed_seconds=sum(elapsed_values) if elapsed_values else None,
        )
        return Matrix(
            project_id=project_id,
            workflow_id=workflow_id,
            steps=steps,
            rows=rows,
            summary=summary,
        )


def cell_detail(service: RuntimeService, execution_id: str) -> dict[str, Any]:
    """Full detail for one cell: timestamps, duration, output, and artifacts.

    This is a read-only projection over ``StepExecution``/``Artifact`` intended
    for the matrix's cell detail panel. It performs no state changes.
    """

    execution = service.get_execution(execution_id)
    run = service.get_run(execution.run_id)
    artifacts = [
        {
            "id": artifact.id,
            "name": artifact.name,
            "path": artifact.path,
            "media_type": artifact.media_type,
            "size_bytes": artifact.size_bytes,
        }
        for artifact in run.artifacts
        if artifact.step_execution_id == execution.id
    ]
    return {
        "execution_id": execution.id,
        "step": execution.step_definition.key,
        "status": execution.status,
        "attempt": execution.attempt,
        "started_at": _iso(execution.started_at),
        "finished_at": _iso(execution.finished_at),
        "duration_seconds": _duration_seconds(execution.started_at, execution.finished_at),
        "exit_code": execution.exit_code,
        "command": list(execution.command_json),
        "stdout": execution.stdout,
        "stderr": execution.stderr,
        "error": execution.error,
        "artifacts": artifacts,
    }


def export_matrix_csv(matrix: Matrix, path: str | Path) -> Path:
    """Export the current matrix (one row per dataset, one column per step)."""

    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    fieldnames = ["dataset_id", "dataset_name", "run_status", *matrix.steps]
    with temporary.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for row in matrix.rows:
            record = {
                "dataset_id": row.dataset_external_id,
                "dataset_name": row.dataset_name or "",
                "run_status": row.run_status,
            }
            record.update({step: row.cells[step].status for step in matrix.steps})
            writer.writerow(record)
    os.replace(temporary, destination)
    return destination


_DETAIL_FIELDS = [
    "dataset_id",
    "step",
    "status",
    "attempt",
    "started_at",
    "finished_at",
    "duration_seconds",
    "exit_code",
    "result_summary",
    "error_summary",
    "artifact_count",
]
DETAIL_FIELDS = _DETAIL_FIELDS


def export_matrix_details_csv(matrix: Matrix, path: str | Path) -> Path:
    """Export one detailed row per dataset/step cell."""

    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=_DETAIL_FIELDS)
        writer.writeheader()
        for row in matrix.rows:
            for step in matrix.steps:
                cell = row.cells[step]
                writer.writerow({
                    "dataset_id": row.dataset_external_id,
                    "step": step,
                    "status": cell.status,
                    "attempt": cell.attempt,
                    "started_at": cell.started_at or "",
                    "finished_at": cell.finished_at or "",
                    "duration_seconds": cell.duration_seconds if cell.duration_seconds is not None else "",
                    "exit_code": cell.exit_code if cell.exit_code is not None else "",
                    "result_summary": cell.result_summary or "",
                    "error_summary": cell.error_summary or "",
                    "artifact_count": cell.artifact_count,
                })
    os.replace(temporary, destination)
    return destination


__all__ = [
    "DETAIL_FIELDS",
    "MATRIX_STATUSES",
    "Matrix",
    "MatrixCell",
    "MatrixReader",
    "MatrixQueryService",
    "MatrixRow",
    "MatrixSummary",
    "cell_detail",
    "export_matrix_csv",
    "export_matrix_details_csv",
]
