"""Read-only importer for existing AVICA result trees."""

from __future__ import annotations

import csv
import json

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

from .models import Project, WorkflowDefinition
from .service import RuntimeService, Status

DEFAULT_AVICA_STEPS = (
    "preprocess_fitsidi",
    "fits_to_ms",
    "phaseshift",
    "avica_avg",
    "avicameta_ms",
    "avica_snr",
    "avica_fill_input",
    "avica_split_ms",
    "rpicard",
)
_RESULT_SUFFIX = "_result.csv"



@dataclass(frozen=True)
class AvicaImportResult:
    project: Project
    workflow: WorkflowDefinition
    dataset_count: int
    artifact_count: int
    skipped_artifact_count: int


def parse_avica_config(path: str | Path) -> dict[str, Any]:
    """Read AVICA's simple ``key=value`` config without importing AVICA."""
    source = Path(path)
    if not source.is_file():
        return {}
    values: dict[str, Any] = {}
    for line in source.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        if not key:
            continue
        values[key] = _coerce_config_value(value)
    return values


def _coerce_config_value(value: str) -> Any:
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    try:
        return int(value)
    except ValueError:
        try:
            return float(value)
        except ValueError:
            return value


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _parse_json(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _status_for_row(row: dict[str, str]) -> str:
    successes = _parse_json(row.get("success"))
    failed_count = _integer(row.get("failed_count"))
    if failed_count > 0:
        return Status.FAILED.value
    if isinstance(successes, list) and any(item is False for item in successes):
        return Status.FAILED.value
    return Status.SUCCEEDED.value


def _integer(value: str | None) -> int:
    try:
        return int(value or "0")
    except ValueError:
        return 0


def _latest_rows(rows: Iterable[dict[str, str]]) -> dict[str, tuple[int, dict[str, str]]]:
    latest: dict[str, tuple[int, dict[str, str]]] = {}
    attempts: dict[str, int] = {}
    for row in rows:
        key = (row.get("name") or "").strip()
        if key:
            attempts[key] = attempts.get(key, 0) + 1
            latest[key] = (attempts[key], row)
    return latest


def _detail_artifacts(detail: Any, result_csv: Path) -> Iterable[tuple[str, Path]]:
    if not isinstance(detail, dict):
        return ()
    values: list[tuple[str, Path]] = []
    for band, raw_path in detail.items():
        if not isinstance(raw_path, str):
            continue
        candidate = _artifact_path(raw_path, result_csv.parent)
        if candidate is not None:
            values.append((f"{band}: {candidate.name}", candidate))
    return values


def _artifact_path(raw_path: str, base: Path) -> Path | None:
    value = raw_path.strip()
    if not value or value == "done":
        return None
    if not value.startswith("/") and ",/" in value:
        # Failed rPicard detail values can prefix a real absolute error/log
        # path with a comma-separated source list. A slash in an ordinary
        # relative path (e.g. ``outputs/VLBI.ms``) is not such a path.
        value = value[value.rfind(",/") + 1 :]
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def _artifact_media_type(path: Path) -> str | None:
    if path.suffix == ".csv":
        return "text/csv"
    if path.suffix == ".uvf":
        return "application/fits"
    return None


def _discover_result_csvs(root: Path, reductions: Path) -> list[tuple[Path, dict[str, str]]]:
    """Result CSVs under ``reductions`` via the alfrd.yaml ``result_csv`` patterns."""
    from alfrd.avica_layout import layout_patterns, result_csvs, scan_project_codes

    try:
        patterns = layout_patterns(root)
    except Exception:  # noqa: BLE001 - no/invalid alfrd.yaml: built-in patterns
        patterns = None
    try:
        known = [code.code for code in scan_project_codes(root, reductions, patterns)]
    except Exception:  # noqa: BLE001
        known = []
    items = result_csvs(root, reductions, patterns, known)
    out = []
    for item in items:
        path = (root / item["file"]).resolve()
        if not item.get("target") and path.name.endswith(_RESULT_SUFFIX):
            continue
        out.append((path, item))
    return sorted(out, key=lambda pair: str(pair[0]))


def import_avica_run(
    service: RuntimeService,
    reductions_dir: str | Path,
    *,
    project_name: str,
    steps: Sequence[str] = DEFAULT_AVICA_STEPS,
    project_root: str | Path | None = None,
    workflow_name: str = "avica",
) -> AvicaImportResult:
    """Attach per-target AVICA histories to runtime persistence read-only.

    Each ``<TARGET>_result.csv`` yields one dataset and one terminal imported
    run. Repeated rows collapse to their final CSV row; unobserved declared
    steps are persisted as skipped so the matrix is complete.
    """
    reductions = Path(reductions_dir).expanduser().resolve()
    if not reductions.is_dir():
        raise FileNotFoundError(reductions)
    source_root = Path(project_root).expanduser().resolve() if project_root else reductions.parent
    found = _discover_result_csvs(source_root, reductions)
    if not found:
        raise ValueError(f"No result__<target>__<code>__<workdir>.csv (or *{_RESULT_SUFFIX}) files found in {reductions}")
    result_files = [path for path, _ in found]
    if len(set(steps)) != len(steps) or not steps:
        raise ValueError("steps must be a non-empty sequence of unique keys")
    if any(project.name == project_name for project in service.list_projects()):
        raise ValueError(
            f"project {project_name!r} already exists; "
            "use a new --project name or a fresh runtime DB"
        )

    parameters = parse_avica_config(reductions.parent / "avica.inp")
    project = service.create_project(
        project_name,
        source_root,
        "Imported AVICA output history (read-only source tree).",
    )
    workflow = service.create_workflow(
        project.id,
        workflow_name,
        [{"key": key, "command": ["external-avica", key]} for key in steps],
        description="Imported AVICA result CSV status history.",
        parameters=parameters,
    )

    artifact_count = 0
    skipped_artifact_count = 0
    per_target: dict[str, int] = {}
    for _, info in found:
        per_target[info["target"]] = per_target.get(info["target"], 0) + 1
    for result_csv, info in found:
        target = info["target"]
        code, workdir = info.get("project_code") or "", info.get("workdir") or ""
        # One result file per (target, project code, work dir) with newer AVICA;
        # the dataset id stays the bare target unless the target has several files.
        external_id = target if per_target[target] == 1 or not code else f"{target}@{code}/{workdir}"
        with result_csv.open(newline="", encoding="utf-8-sig") as stream:
            rows = list(csv.DictReader(stream))
        latest = _latest_rows(rows)
        unknown = sorted(set(latest) - set(steps))
        if unknown:
            raise ValueError(
                f"{result_csv} contains steps not declared for this import: {', '.join(unknown)}"
            )
        metadata = {"result_csv": str(result_csv), "target": target}
        if code:
            metadata["project_code"] = code
        if workdir:
            metadata["workdir"] = workdir
        dataset = service.create_dataset(
            project.id,
            external_id,
            name=target,
            uri=str(result_csv),
            metadata=metadata,
        )
        execution_rows: list[dict[str, Any]] = []
        starts: list[datetime] = []
        finishes: list[datetime] = []
        errors: list[str] = []
        has_failed_step = False
        for step, (attempt, row) in latest.items():
            started_at = _parse_datetime(row.get("start_stamp"))
            finished_at = _parse_datetime(row.get("end_stamp"))
            if started_at:
                starts.append(started_at)
            if finished_at:
                finishes.append(finished_at)
            detail = row.get("detail") or ""
            description = row.get("desc") or ""
            status = _status_for_row(row)
            item: dict[str, Any] = {
                "key": step,
                "attempt": attempt,
                "status": status,
                "started_at": started_at,
                "finished_at": finished_at,
                "stdout": description if status == Status.SUCCEEDED.value else None,
                "stderr": description if status == Status.FAILED.value else None,
                "error": description if status == Status.FAILED.value else None,
            }
            execution_rows.append(item)
            if status == Status.FAILED.value:
                has_failed_step = True
                if description:
                    errors.append(description)
        run_status = Status.FAILED.value if has_failed_step else Status.SUCCEEDED.value
        run = service.import_historical_run(
            workflow.id,
            dataset.id,
            working_directory=result_csv.parent,
            status=run_status,
            executions=execution_rows,
            started_at=min(starts) if starts else None,
            finished_at=max(finishes) if finishes else None,
            error="\n".join(errors) or None,
            parameters={"source_result_csv": str(result_csv)},
        )
        execution_by_key = {item.step_definition.key: item for item in run.step_executions}
        artifact_specs = [("result CSV", result_csv)]
        for step, (_, row) in latest.items():
            artifact_specs.extend(_detail_artifacts(_parse_json(row.get("detail")), result_csv))
        for name, artifact_path in artifact_specs:
            if not artifact_path.exists():
                skipped_artifact_count += 1
                continue
            step_execution_id = None
            if name != "result CSV":
                # The name is prefixed with the detail map's band, while the
                # associated step is available from this loop only for detail
                # products. Associate result CSV with the run itself.
                for step, (_, row) in latest.items():
                    if (name, artifact_path) in _detail_artifacts(_parse_json(row.get("detail")), result_csv):
                        step_execution_id = execution_by_key[step].id
                        break
            service.record_artifact(
                run.id,
                artifact_path,
                step_execution_id=step_execution_id,
                name=name,
                media_type=_artifact_media_type(artifact_path),
                metadata={"source": "avica-detail" if step_execution_id else "avica-result-csv"},
                write_manifest=False,
            )
            artifact_count += 1

    return AvicaImportResult(
        project=project,
        workflow=service.get_workflow(workflow.id),
        dataset_count=len(result_files),
        artifact_count=artifact_count,
        skipped_artifact_count=skipped_artifact_count,
    )


__all__ = [
    "AvicaImportResult",
    "DEFAULT_AVICA_STEPS",
    "import_avica_run",
    "parse_avica_config",
]
