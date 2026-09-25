"""Safe declarative project summaries derived from manifests and runtime rows."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from alfrd.gui.services import resolve_selected_manifest
from alfrd.manifest import ManifestError, load_manifest

CONFIG_COLUMNS = (
    {"key": "step", "label": "Step"},
    {"key": "parameter", "label": "Parameter"},
    {"key": "source", "label": "Source"},
    {"key": "value", "label": "Value"},
)
RESULT_LABELS = {
    "dataset": "Dataset",
    "step": "Step",
    "status": "Status",
    "attempt": "Attempt",
    "duration_seconds": "Duration (seconds)",
    "finished_at": "Finished",
    "result_summary": "Result",
    "error_summary": "Error",
    "artifact_count": "Artifacts",
}
DEFAULT_RESULT_COLUMNS = tuple(RESULT_LABELS)


def _table(title: str, columns, rows=None, error: str | None = None) -> dict[str, Any]:
    return {
        "title": title,
        "columns": [dict(column) for column in columns],
        "rows": list(rows or []),
        "error": error,
    }


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _duration(started: datetime | None, finished: datetime | None) -> float | None:
    if started is None or finished is None:
        return None
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    if finished.tzinfo is None:
        finished = finished.replace(tzinfo=timezone.utc)
    return (finished - started).total_seconds()


def _last_line(value: str | None) -> str | None:
    lines = [line.strip() for line in (value or "").splitlines() if line.strip()]
    return lines[-1] if lines else None


def build_summaries(project: dict[str, Any], runtime_service) -> dict[str, dict[str, Any]]:
    """Build the two dashboard tables without importing consumer code."""

    config = _table("Configuration snapshot", CONFIG_COLUMNS)
    result_columns = list(DEFAULT_RESULT_COLUMNS)
    result_title = "Latest results"
    root_path = project.get("root_path")
    manifest = None
    if not root_path:
        config["error"] = "No project manifest path is available."
    else:
        try:
            manifest = load_manifest(resolve_selected_manifest(Path(root_path)))
        except (ManifestError, OSError, ValueError) as error:
            config["error"] = str(error)

    if manifest is not None:
        raw_summaries = manifest.extra.get("summaries", {})
        config_spec = raw_summaries.get("config", {})
        config["title"] = config_spec.get("title", config["title"])
        config["rows"] = [
            {
                "step": row["step"],
                "parameter": row["parameter"],
                "source": row.get("source", "manifest snapshot"),
                "value": row["value"],
            }
            for row in config_spec.get("rows", [])
        ]
        result_spec = raw_summaries.get("result", {})
        result_title = result_spec.get("title", result_title)
        result_columns = list(result_spec.get("columns", result_columns))

    result = _table(
        result_title,
        ({"key": key, "label": RESULT_LABELS[key]} for key in result_columns),
    )
    if runtime_service is None:
        result["error"] = "Result persistence is not configured."
        return {"config": config, "result": result}

    try:
        datasets = {
            item.id: item.external_id
            for item in runtime_service.list_datasets(project["id"])
        }
        runs = runtime_service.list_runs(project_id=project["id"])
    except Exception as error:
        # The dashboard must remain available if runtime rows are stale or an
        # independently managed catalog project has no matching runtime row.
        result["error"] = f"Could not read runtime results: {error}"
        return {"config": config, "result": result}

    rows: list[dict[str, Any]] = []
    # list_runs is oldest first, matching the matrix's latest-run view.
    latest_runs = {(run.workflow_id, run.dataset_id): run for run in runs}
    for run in latest_runs.values():
        latest = {}
        for execution in run.step_executions:
            key = execution.step_definition.key
            current = latest.get(key)
            if current is None or execution.attempt > current.attempt:
                latest[key] = execution
        for key, execution in sorted(latest.items(), key=lambda item: item[1].sequence):
            values = {
                "dataset": datasets.get(run.dataset_id, run.dataset_id),
                "step": key,
                "status": execution.status,
                "attempt": execution.attempt,
                "duration_seconds": _duration(execution.started_at, execution.finished_at),
                "finished_at": _iso(execution.finished_at),
                "result_summary": _last_line(execution.stdout),
                "error_summary": execution.error,
                "artifact_count": sum(
                    artifact.step_execution_id == execution.id for artifact in run.artifacts
                ),
            }
            rows.append({column: values[column] for column in result_columns})
    result["rows"] = rows
    return {"config": config, "result": result}


def _json_value(value: Any, fallback: Any = None) -> Any:
    if value in (None, ""):
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value


def _integer(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _seconds(started: Any, finished: Any) -> float | None:
    start = _timestamp(started) if not isinstance(started, datetime) else started
    end = _timestamp(finished) if not isinstance(finished, datetime) else finished
    if start is None or end is None:
        return None
    return max(0.0, (end - start).total_seconds())


def _avica_status(row: dict[str, Any] | None) -> str:
    if row is None:
        return "pending"
    ok = _integer(row.get("success_count"))
    failed = _integer(row.get("failed_count"))
    if ok and failed:
        return "partial"
    if failed:
        return "failed"
    return "ok" if ok else "pending"


def _note(row: dict[str, Any]) -> str:
    description = _json_value(row.get("desc"), [])
    if isinstance(description, list) and description:
        return " ".join(str(description[0]).split())
    if description:
        return " ".join(str(description).split())
    detail = _json_value(row.get("detail"))
    if detail:
        return " ".join(str(detail).split())
    return ""


def _avica_attempt(row: dict[str, Any], attempt: int) -> dict[str, Any]:
    success = _json_value(row.get("success"), [])
    return {
        "step": str(row.get("name") or ""),
        "attempt": attempt,
        "status": _avica_status(row),
        "success_count": _integer(row.get("success_count")),
        "failed_count": _integer(row.get("failed_count")),
        "items": success if isinstance(success, list) else [],
        "duration_seconds": _seconds(row.get("start_stamp"), row.get("end_stamp")),
        "started_at": row.get("start_stamp") or None,
        "finished_at": row.get("end_stamp") or None,
        "note": _note(row),
    }


def _avica_dataset_summary(dataset, workflow, result_csv: Path) -> dict[str, Any] | None:
    try:
        with result_csv.open(newline="", encoding="utf-8-sig") as stream:
            raw_rows = [row for row in csv.DictReader(stream) if (row.get("name") or "").strip()]
    except (OSError, csv.Error):
        return None
    if not raw_rows or "success_count" not in raw_rows[0]:
        return None

    grouped: dict[str, list[dict[str, Any]]] = {}
    for raw in raw_rows:
        step = str(raw.get("name") or "").strip()
        grouped.setdefault(step, []).append(raw)

    # Match ``avica pipe result``: the ladder contains steps that actually
    # participate in the append-only result CSV. The wider matrix still keeps
    # every declared workflow column and shows an absent step as skipped.
    ordered_steps = [step.key for step in workflow.steps if step.key in grouped]
    ordered_steps.extend(step for step in grouped if step not in ordered_steps)
    history = [
        _avica_attempt(raw, attempt)
        for step in ordered_steps
        for attempt, raw in enumerate(grouped.get(step, []), start=1)
    ]
    rows = []
    for position, step in enumerate(ordered_steps, start=1):
        attempts = grouped.get(step, [])
        latest = _avica_attempt(attempts[-1], len(attempts)) if attempts else {
            "step": step, "attempt": 0, "status": "pending", "success_count": 0,
            "failed_count": 0, "items": [], "duration_seconds": None,
            "started_at": None, "finished_at": None, "note": "",
        }
        latest["position"] = position
        latest["attempt_count"] = len(attempts)
        rows.append(latest)

    return _dataset_summary_payload(dataset, workflow, rows, history, "AVICA result CSV")


_RUNTIME_DISPLAY = {
    "succeeded": "ok", "failed": "failed", "pending": "pending", "running": "running",
    "cancelled": "interrupted", "interrupted": "interrupted", "skipped": "skipped",
}


def _runtime_attempt(execution, attempt: int) -> dict[str, Any]:
    status = _RUNTIME_DISPLAY.get(execution.status, execution.status)
    successful = status == "ok"
    failed = status == "failed"
    return {
        "step": execution.step_definition.key,
        "attempt": attempt,
        "status": status,
        "success_count": 1 if successful else 0,
        "failed_count": 1 if failed else 0,
        "items": [successful] if successful or failed else [],
        "duration_seconds": _seconds(execution.started_at, execution.finished_at),
        "started_at": _iso(execution.started_at),
        "finished_at": _iso(execution.finished_at),
        "note": _last_line(execution.error or execution.stdout or execution.stderr) or "",
    }


def _runtime_dataset_summary(service, dataset, workflow) -> dict[str, Any]:
    runs = [run for run in service.list_runs(dataset_id=dataset.id) if run.workflow_id == workflow.id]
    grouped: dict[str, list[dict[str, Any]]] = {step.key: [] for step in workflow.steps}
    for run in runs:
        for execution in run.step_executions:
            values = grouped.setdefault(execution.step_definition.key, [])
            values.append(_runtime_attempt(execution, len(values) + 1))

    history = [item for step in grouped.values() for item in step]
    rows = []
    for position, step in enumerate(workflow.steps, start=1):
        attempts = grouped.get(step.key, [])
        latest = dict(attempts[-1]) if attempts else {
            "step": step.key, "attempt": 0, "status": "pending", "success_count": 0,
            "failed_count": 0, "items": [], "duration_seconds": None,
            "started_at": None, "finished_at": None, "note": "",
        }
        latest["position"] = position
        latest["attempt_count"] = len(attempts)
        rows.append(latest)
    return _dataset_summary_payload(dataset, workflow, rows, history, "ALFRD runtime")


def _dataset_summary_payload(dataset, workflow, rows, history, source: str) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    next_step = next((row["step"] for row in rows if row["status"] != "ok"), None)
    return {
        "dataset_id": dataset.id,
        "dataset_external_id": dataset.external_id,
        "dataset_name": dataset.name,
        "workflow": workflow.name,
        "source": source,
        "steps": rows,
        "history": history,
        "status_counts": counts,
        "complete": bool(rows) and next_step is None,
        "next_step": next_step,
        "attempt_count": len(history),
    }


def build_dataset_summary(runtime_service, project_id: str, workflow_id: str, dataset_id: str) -> dict[str, Any]:
    """Build a terminal-style latest result plus collapsed attempt history."""

    dataset = runtime_service.get_dataset(dataset_id)
    workflow = runtime_service.get_workflow(workflow_id)
    if dataset.project_id != project_id or workflow.project_id != project_id:
        raise ValueError("dataset and workflow must belong to the requested project")
    result_path = dataset.metadata_json.get("result_csv") if dataset.metadata_json else None
    if result_path:
        avica = _avica_dataset_summary(dataset, workflow, Path(result_path))
        if avica is not None:
            return avica
    return _runtime_dataset_summary(runtime_service, dataset, workflow)


__all__ = ["build_dataset_summary", "build_summaries"]
