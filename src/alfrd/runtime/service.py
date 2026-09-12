from __future__ import annotations

import csv
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from alfrd.manifest import ProjectManifest, load_manifest as load_project_manifest

from .models import (
    Artifact,
    AuditEvent,
    Dataset,
    Project,
    Run,
    StepDefinition,
    StepExecution,
    WorkflowDefinition,
    new_id,
    utcnow,
)
from .store import SCHEMA_VERSION, RuntimeStore


class Status(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    SKIPPED = "skipped"


class InvalidTransition(ValueError):
    pass


class RuntimeNotFound(LookupError):
    pass


class DuplicateRunError(RuntimeError):
    """Raised when a dataset already has an active (non-terminal) run."""


class ParameterValidationError(ValueError):
    """Raised when start parameters fail validation before enqueue."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


ACTIVE_RUN_STATUSES = {Status.PENDING.value, Status.RUNNING.value}


RUN_TRANSITIONS = {
    Status.PENDING.value: {Status.RUNNING.value, Status.CANCELLED.value},
    Status.RUNNING.value: {
        Status.SUCCEEDED.value,
        Status.FAILED.value,
        Status.CANCELLED.value,
        Status.INTERRUPTED.value,
    },
    Status.FAILED.value: {Status.PENDING.value},
    Status.INTERRUPTED.value: {Status.PENDING.value},
    Status.SUCCEEDED.value: set(),
    Status.CANCELLED.value: set(),
}
EXECUTION_TRANSITIONS = {
    Status.PENDING.value: {Status.RUNNING.value, Status.CANCELLED.value, Status.SKIPPED.value},
    Status.RUNNING.value: {
        Status.SUCCEEDED.value,
        Status.FAILED.value,
        Status.CANCELLED.value,
        Status.INTERRUPTED.value,
    },
    Status.SUCCEEDED.value: set(),
    Status.FAILED.value: set(),
    Status.CANCELLED.value: set(),
    Status.INTERRUPTED.value: set(),
    Status.SKIPPED.value: set(),
}


def _validate_run_directory_component(identifier: str) -> None:
    """Reject a run identifier that could escape the project's ``runs`` root.

    ``create_run`` joins a caller-supplied or generated ``run_id`` directly
    below ``<project_root>/runs/`` whenever an explicit ``working_directory``
    is not supplied. Without this check a run id containing path separators
    or ``..`` segments (e.g. supplied through a future public API) could
    write run state and artifacts outside the project's directory tree.
    """
    candidate = Path(identifier)
    if (
        not identifier
        or candidate.is_absolute()
        or identifier in {".", ".."}
        or "/" in identifier
        or "\\" in identifier
        or len(candidate.parts) != 1
    ):
        raise ValueError(f"Invalid run id: {identifier!r}")


def _status(value: Status | str) -> str:
    try:
        return Status(value).value
    except ValueError as exc:
        raise InvalidTransition(f"unknown status {value!r}") from exc


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:  # SQLite may return a naive value for a timezone-aware column.
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


class RuntimeService:
    """Transactional runtime operations independent of pipeline orchestration."""

    def __init__(self, store: RuntimeStore) -> None:
        self.store = store

    def create_project(self, name: str, root_path: str | Path, description: str | None = None) -> Project:
        root = Path(root_path).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        with self.store.session() as session:
            project = Project(name=name, root_path=str(root), description=description)
            session.add(project)
            session.flush()
            self._audit_entity(session, "project", project.id, "created")
            session.expunge(project)
            return project

    def list_projects(self) -> list[Project]:
        with self.store.session() as session:
            return list(session.scalars(select(Project).order_by(Project.name)))

    def get_project_by_name(self, name: str) -> Project:
        with self.store.session() as session:
            project = session.scalar(select(Project).where(Project.name == name))
            if project is None:
                raise RuntimeNotFound(f"project {name!r} not found")
            return project

    def register_manifest(
        self,
        manifest: ProjectManifest | str | Path,
        *,
        root_path: str | Path | None = None,
    ) -> tuple[Project, list[WorkflowDefinition]]:
        """Register a discovered project without importing consumer code.

        Each manifest entrypoint becomes a one-step command workflow. Typed
        Python workflows can use the same persisted definition through
        ``RuntimePipelineRunner`` when their step names match.
        """

        document = (
            manifest if isinstance(manifest, ProjectManifest) else load_project_manifest(manifest)
        )
        root = root_path or (document.path.parent if document.path is not None else None)
        if root is None:
            raise ValueError("root_path is required for an in-memory manifest")
        description = document.extra.get("description")
        project = self.create_project(
            document.name,
            root,
            description if isinstance(description, str) else None,
        )
        workflows = [
            self.create_workflow(
                project.id,
                entrypoint.name,
                [{"key": entrypoint.name, "command": entrypoint.cmd}],
            )
            for entrypoint in document.entrypoint
        ]
        return project, workflows

    def create_workflow(
        self,
        project_id: str,
        name: str,
        steps: Sequence[Mapping[str, Any]],
        *,
        version: int = 1,
        description: str | None = None,
        parameters: Mapping[str, Any] | None = None,
    ) -> WorkflowDefinition:
        if not steps:
            raise ValueError("a workflow must define at least one step")
        seen: set[str] = set()
        with self.store.session() as session:
            if session.get(Project, project_id) is None:
                raise RuntimeNotFound(f"project {project_id!r} not found")
            workflow = WorkflowDefinition(
                project_id=project_id,
                name=name,
                version=version,
                description=description,
                parameters_json=dict(parameters or {}),
            )
            for position, specification in enumerate(steps):
                key = str(specification["key"])
                command = specification.get("command")
                if not key or key in seen:
                    raise ValueError(f"duplicate or empty step key {key!r}")
                if not isinstance(command, (list, tuple)) or not command or not all(
                    isinstance(part, str) for part in command
                ):
                    raise ValueError(f"step {key!r} command must be a non-empty string sequence")
                seen.add(key)
                workflow.steps.append(StepDefinition(
                    key=key,
                    position=position,
                    command_json=list(command),
                    parameters_json=dict(specification.get("parameters", {})),
                ))
            session.add(workflow)
            session.flush()
            self._audit_entity(session, "workflow_definition", workflow.id, "created")
            session.expunge(workflow)
            return workflow

    def get_project(self, project_id: str) -> Project:
        with self.store.session() as session:
            project = session.get(Project, project_id)
            if project is None:
                raise RuntimeNotFound(f"project {project_id!r} not found")
            return project

    def get_workflow(self, workflow_id: str) -> WorkflowDefinition:
        with self.store.session() as session:
            workflow = session.scalar(select(WorkflowDefinition).options(
                selectinload(WorkflowDefinition.steps)
            ).where(WorkflowDefinition.id == workflow_id))
            if workflow is None:
                raise RuntimeNotFound(f"workflow {workflow_id!r} not found")
            return workflow

    def list_workflows(self, project_id: str) -> list[WorkflowDefinition]:
        with self.store.session() as session:
            return list(session.scalars(select(WorkflowDefinition).options(
                selectinload(WorkflowDefinition.steps)
            ).where(WorkflowDefinition.project_id == project_id).order_by(
                WorkflowDefinition.name, WorkflowDefinition.version
            )))

    def create_dataset(
        self,
        project_id: str,
        external_id: str,
        *,
        name: str | None = None,
        uri: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Dataset:
        if not external_id:
            raise ValueError("external_id cannot be empty")
        with self.store.session() as session:
            if session.get(Project, project_id) is None:
                raise RuntimeNotFound(f"project {project_id!r} not found")
            dataset = Dataset(
                project_id=project_id,
                external_id=external_id,
                name=name,
                uri=uri,
                metadata_json=dict(metadata or {}),
            )
            session.add(dataset)
            session.flush()
            self._audit_entity(session, "dataset", dataset.id, "created")
            session.expunge(dataset)
            return dataset

    def list_datasets(self, project_id: str) -> list[Dataset]:
        with self.store.session() as session:
            return list(session.scalars(
                select(Dataset).where(Dataset.project_id == project_id).order_by(Dataset.external_id)
            ))

    def get_dataset(self, dataset_id: str) -> Dataset:
        with self.store.session() as session:
            dataset = session.get(Dataset, dataset_id)
            if dataset is None:
                raise RuntimeNotFound(f"dataset {dataset_id!r} not found")
            return dataset

    def get_workflow_by_name(self, project_id: str, name: str) -> WorkflowDefinition:
        matches = [
            workflow for workflow in self.list_workflows(project_id) if workflow.name == name
        ]
        if not matches:
            raise RuntimeNotFound(f"workflow {name!r} not found in project {project_id!r}")
        return matches[-1]

    def get_dataset_by_external_id(self, project_id: str, external_id: str) -> Dataset:
        with self.store.session() as session:
            dataset = session.scalar(select(Dataset).where(
                Dataset.project_id == project_id, Dataset.external_id == external_id
            ))
            if dataset is None:
                raise RuntimeNotFound(
                    f"dataset {external_id!r} not found in project {project_id!r}"
                )
            return dataset

    def active_run_for_dataset(self, workflow_id: str, dataset_id: str) -> Run | None:
        """Return the currently pending/running run for this workflow+dataset, if any."""
        with self.store.session() as session:
            run = session.scalar(
                select(Run)
                .where(
                    Run.workflow_id == workflow_id,
                    Run.dataset_id == dataset_id,
                    Run.status.in_(ACTIVE_RUN_STATUSES),
                )
                .order_by(Run.created_at.desc())
            )
            if run is None:
                return None
            return self.get_run(run.id)

    def validate_start_parameters(
        self,
        workflow_id: str,
        dataset_id: str,
        *,
        parameters: Mapping[str, Any] | None = None,
    ) -> list[str]:
        """Validate parameters before enqueue without importing consumer code.

        Confirms the workflow and dataset exist, belong to the same project,
        and that supplied parameters are a JSON-serializable mapping.
        """
        errors: list[str] = []
        try:
            workflow = self.get_workflow(workflow_id)
        except RuntimeNotFound as exc:
            return [str(exc)]
        try:
            dataset = self.get_dataset(dataset_id)
        except RuntimeNotFound as exc:
            return [str(exc)]
        if workflow.project_id != dataset.project_id:
            errors.append("workflow and dataset must belong to the same project")
        if parameters is not None:
            if not isinstance(parameters, Mapping):
                errors.append("parameters must be a mapping of name to value")
            else:
                for key, value in parameters.items():
                    if not isinstance(key, str) or not key:
                        errors.append(f"parameter name {key!r} must be a non-empty string")
                    try:
                        json.dumps(value)
                    except (TypeError, ValueError):
                        errors.append(f"parameter {key!r} is not JSON-serializable")
        return errors

    def start_run(
        self,
        workflow_id: str,
        dataset_id: str,
        *,
        parameters: Mapping[str, Any] | None = None,
        working_directory: str | Path | None = None,
        allow_concurrent: bool = False,
    ) -> Run:
        """Validate, guard against duplicate runs, and enqueue a new run.

        This is the sole entry point browser/CLI control surfaces should use to
        start a workflow; it never executes pipeline code itself.
        """
        errors = self.validate_start_parameters(workflow_id, dataset_id, parameters=parameters)
        if errors:
            raise ParameterValidationError(errors)
        if not allow_concurrent:
            active = self.active_run_for_dataset(workflow_id, dataset_id)
            if active is not None:
                raise DuplicateRunError(
                    f"dataset {dataset_id!r} already has an active run {active.id!r} "
                    f"in status {active.status!r}"
                )
        run = self.create_run(
            workflow_id,
            dataset_id,
            parameters=parameters,
            working_directory=working_directory,
        )
        with self.store.session() as session:
            self._audit(session, run.id, "start_requested", to_status=Status.PENDING.value)
        return self.get_run(run.id)

    def create_run(
        self,
        workflow_id: str,
        dataset_id: str,
        *,
        parameters: Mapping[str, Any] | None = None,
        parent_run_id: str | None = None,
        run_id: str | None = None,
        working_directory: str | Path | None = None,
    ) -> Run:
        identifier = run_id or new_id()
        if working_directory is None:
            _validate_run_directory_component(identifier)
        with self.store.session() as session:
            workflow = session.scalar(select(WorkflowDefinition).options(
                selectinload(WorkflowDefinition.steps), selectinload(WorkflowDefinition.project)
            ).where(WorkflowDefinition.id == workflow_id))
            dataset = session.get(Dataset, dataset_id)
            if workflow is None:
                raise RuntimeNotFound(f"workflow {workflow_id!r} not found")
            if dataset is None:
                raise RuntimeNotFound(f"dataset {dataset_id!r} not found")
            if workflow.project_id != dataset.project_id:
                raise ValueError("workflow and dataset must belong to the same project")
            workdir = Path(working_directory).expanduser().resolve() if working_directory else (
                Path(workflow.project.root_path) / "runs" / identifier
            )
            workdir.mkdir(parents=True, exist_ok=True)
            run = Run(
                id=identifier,
                workflow_id=workflow_id,
                dataset_id=dataset_id,
                parent_run_id=parent_run_id,
                working_directory=str(workdir),
                parameters_json=dict(parameters or {}),
            )
            run.step_executions = [
                StepExecution(
                    step_definition_id=step.id,
                    sequence=step.position,
                    command_json=list(step.command_json),
                    cwd=str(workdir),
                )
                for step in workflow.steps
            ]
            session.add(run)
            session.flush()
            self._audit(session, run.id, "created", to_status=run.status)
        self._write_manifest(identifier)
        return self.get_run(identifier)

    def get_run(self, run_id: str) -> Run:
        with self.store.session() as session:
            run = session.scalar(
                select(Run)
                .options(
                    selectinload(Run.step_executions).selectinload(StepExecution.step_definition),
                    selectinload(Run.artifacts),
                )
                .where(Run.id == run_id)
            )
            if run is None:
                raise RuntimeNotFound(f"run {run_id!r} not found")
            return run

    def list_runs(self, *, project_id: str | None = None, dataset_id: str | None = None) -> list[Run]:
        statement = select(Run).options(
            selectinload(Run.step_executions).selectinload(StepExecution.step_definition),
            selectinload(Run.artifacts),
        )
        if project_id is not None:
            statement = statement.join(WorkflowDefinition).where(
                WorkflowDefinition.project_id == project_id
            )
        if dataset_id is not None:
            statement = statement.where(Run.dataset_id == dataset_id)
        with self.store.session() as session:
            return list(session.scalars(statement.order_by(Run.created_at, Run.id)))

    def get_execution(self, execution_id: str) -> StepExecution:
        with self.store.session() as session:
            execution = session.scalar(select(StepExecution).options(
                selectinload(StepExecution.step_definition)
            ).where(StepExecution.id == execution_id))
            if execution is None:
                raise RuntimeNotFound(f"step execution {execution_id!r} not found")
            return execution

    def next_pending_execution(self, run_id: str) -> StepExecution | None:
        with self.store.session() as session:
            return session.scalar(
                select(StepExecution)
                .options(selectinload(StepExecution.step_definition))
                .where(StepExecution.run_id == run_id, StepExecution.status == Status.PENDING.value)
                .order_by(StepExecution.sequence, StepExecution.attempt.desc())
            )

    def heartbeat(self, run_id: str) -> Run:
        with self.store.session() as session:
            run = session.get(Run, run_id)
            if run is None:
                raise RuntimeNotFound(f"run {run_id!r} not found")
            if run.status != Status.RUNNING.value:
                raise InvalidTransition("only running runs accept heartbeats")
            run.heartbeat_at = utcnow()
        self._write_manifest(run_id)
        return self.get_run(run_id)

    def transition_run(self, run_id: str, status: Status | str, *, error: str | None = None) -> Run:
        target = _status(status)
        now = utcnow()
        with self.store.session() as session:
            run = session.get(Run, run_id)
            if run is None:
                raise RuntimeNotFound(f"run {run_id!r} not found")
            previous = run.status
            if target not in RUN_TRANSITIONS.get(previous, set()):
                raise InvalidTransition(f"run cannot transition from {previous!r} to {target!r}")
            if target == Status.SUCCEEDED.value:
                executions = list(session.scalars(select(StepExecution).where(
                    StepExecution.run_id == run_id
                )))
                latest: dict[str, StepExecution] = {}
                for execution in executions:
                    current = latest.get(execution.step_definition_id)
                    if current is None or execution.attempt > current.attempt:
                        latest[execution.step_definition_id] = execution
                if any(
                    execution.status not in {Status.SUCCEEDED.value, Status.SKIPPED.value}
                    for execution in latest.values()
                ):
                    raise InvalidTransition("run cannot succeed while step executions are unfinished")
            run.status = target
            run.error = error
            run.heartbeat_at = now
            if target == Status.RUNNING.value:
                run.started_at = run.started_at or now
                run.finished_at = None
            elif target in {Status.SUCCEEDED.value, Status.FAILED.value, Status.CANCELLED.value}:
                run.finished_at = now
                run.pid = None
            self._audit(session, run.id, "status_changed", previous, target, {"error": error} if error else {})
        self._write_manifest(run_id)
        return self.get_run(run_id)

    def attach_process(self, run_id: str, *, pid: int, hostname: str | None = None) -> Run:
        """Record subprocess ownership metadata for a running run.

        Used by local worker orchestration so the GUI can display which
        process/host owns an in-flight run.
        """
        with self.store.session() as session:
            run = session.get(Run, run_id)
            if run is None:
                raise RuntimeNotFound(f"run {run_id!r} not found")
            if run.status != Status.RUNNING.value:
                raise InvalidTransition("process ownership can only be attached to a running run")
            run.pid = pid
            run.hostname = hostname
            self._audit(session, run.id, "process_attached", payload={"pid": pid, "hostname": hostname})
        self._write_manifest(run_id)
        return self.get_run(run_id)

    def cancel_run(self, run_id: str, *, reason: str | None = None) -> Run:
        """Safely cancel a pending or running run.

        Callers (Flask routes, CLI) are responsible for collecting explicit
        user confirmation before calling this; the service itself performs no
        confirmation prompt and only records the cancellation intent/result.
        """
        with self.store.session() as session:
            run = session.get(Run, run_id)
            if run is None:
                raise RuntimeNotFound(f"run {run_id!r} not found")
            if run.status not in {Status.PENDING.value, Status.RUNNING.value}:
                raise InvalidTransition(
                    f"run in status {run.status!r} cannot be cancelled"
                )
            self._audit(session, run.id, "cancel_requested", payload={"reason": reason} if reason else {})
        return self.transition_run(run_id, Status.CANCELLED, error=reason)

    def transition_execution(
        self,
        execution_id: str,
        status: Status | str,
        *,
        exit_code: int | None = None,
        stdout: str | None = None,
        stderr: str | None = None,
        error: str | None = None,
        log_path: str | Path | None = None,
    ) -> StepExecution:
        target = _status(status)
        now = utcnow()
        with self.store.session() as session:
            execution = session.get(StepExecution, execution_id)
            if execution is None:
                raise RuntimeNotFound(f"step execution {execution_id!r} not found")
            previous = execution.status
            if target not in EXECUTION_TRANSITIONS.get(previous, set()):
                raise InvalidTransition(f"step cannot transition from {previous!r} to {target!r}")
            run = session.get(Run, execution.run_id)
            if target == Status.RUNNING.value and (run is None or run.status != Status.RUNNING.value):
                raise InvalidTransition("a step can start only while its run is running")
            execution.status = target
            execution.exit_code = exit_code
            execution.stdout = stdout
            execution.stderr = stderr
            execution.error = error
            if log_path is not None:
                execution.log_path = str(log_path)
            if target == Status.RUNNING.value:
                execution.started_at = now
            else:
                execution.finished_at = now
            if run is not None:
                run.heartbeat_at = now
            self._audit(session, execution.run_id, "status_changed", previous, target, {
                "entity": "step_execution", "step_execution_id": execution.id
            })
            run_id = execution.run_id
        self._write_manifest(run_id)
        return self.get_execution(execution_id)

    def record_artifact(
        self,
        run_id: str,
        path: str | Path,
        *,
        step_execution_id: str | None = None,
        name: str | None = None,
        media_type: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Artifact:
        """Persist artifact existence/size/media-type/metadata for one path.

        Both files and directories are supported (a directory artifact, e.g.
        ``kind: directory`` or ``image_collection``, has no single content
        hash to verify against; its ``sha256`` is instead a stable digest of
        its resolved path so identity checks remain deterministic without
        reading every contained file).
        """
        artifact_path = Path(path).expanduser().resolve()
        if artifact_path.is_file():
            digest = hashlib.sha256()
            with artifact_path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            size_bytes = artifact_path.stat().st_size
            sha256 = digest.hexdigest()
        elif artifact_path.is_dir():
            size_bytes = 0
            sha256 = hashlib.sha256(str(artifact_path).encode("utf-8")).hexdigest()
        else:
            raise FileNotFoundError(artifact_path)
        with self.store.session() as session:
            run = session.get(Run, run_id)
            if run is None:
                raise RuntimeNotFound(f"run {run_id!r} not found")
            if step_execution_id:
                execution = session.get(StepExecution, step_execution_id)
                if execution is None or execution.run_id != run_id:
                    raise ValueError("step execution does not belong to run")
            artifact = Artifact(
                run_id=run_id,
                dataset_id=run.dataset_id,
                step_execution_id=step_execution_id,
                name=name or artifact_path.name,
                path=str(artifact_path),
                media_type=media_type,
                size_bytes=size_bytes,
                sha256=sha256,
                metadata_json=dict(metadata or {}),
            )
            session.add(artifact)
            session.flush()
            self._audit(session, run_id, "artifact_recorded", payload={
                "artifact_id": artifact.id, "path": artifact.path
            })
            session.expunge(artifact)
        self._write_manifest(run_id)
        return artifact

    def audit_events(self, run_id: str) -> list[AuditEvent]:
        return self.list_audit_events("run", run_id)

    def list_audit_events(self, aggregate_type: str, aggregate_id: str) -> list[AuditEvent]:
        with self.store.session() as session:
            return list(session.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.aggregate_type == aggregate_type,
                    AuditEvent.aggregate_id == aggregate_id,
                )
                .order_by(AuditEvent.occurred_at, AuditEvent.id)
            ))

    def recover_stale(self, stale_after: timedelta, *, now: datetime | None = None) -> list[Run]:
        current = now or utcnow()
        cutoff = current - stale_after
        recovered_ids: list[str] = []
        with self.store.session() as session:
            stale_runs = list(session.scalars(select(Run).where(
                Run.status == Status.RUNNING.value, Run.heartbeat_at < cutoff
            )))
            for run in stale_runs:
                run.status = Status.INTERRUPTED.value
                run.error = "stale running process recovered"
                run.heartbeat_at = current
                for execution in run.step_executions:
                    if execution.status == Status.RUNNING.value:
                        execution.status = Status.INTERRUPTED.value
                        execution.error = run.error
                        execution.finished_at = current
                self._audit(session, run.id, "stale_recovered", Status.RUNNING.value,
                            Status.INTERRUPTED.value)
                recovered_ids.append(run.id)
        for run_id in recovered_ids:
            self._write_manifest(run_id)
        return [self.get_run(run_id) for run_id in recovered_ids]

    def resume_run(self, run_id: str) -> Run:
        with self.store.session() as session:
            run = session.scalar(select(Run).options(selectinload(Run.step_executions)).where(Run.id == run_id))
            if run is None:
                raise RuntimeNotFound(f"run {run_id!r} not found")
            if run.status not in {Status.FAILED.value, Status.INTERRUPTED.value}:
                raise InvalidTransition("only failed or interrupted runs can be resumed")
            previous = run.status
            latest: dict[str, StepExecution] = {}
            for execution in run.step_executions:
                candidate = latest.get(execution.step_definition_id)
                if candidate is None or execution.attempt > candidate.attempt:
                    latest[execution.step_definition_id] = execution
            for execution in latest.values():
                if execution.status != Status.SUCCEEDED.value:
                    session.add(StepExecution(
                        run_id=run.id,
                        step_definition_id=execution.step_definition_id,
                        sequence=execution.sequence,
                        attempt=execution.attempt + 1,
                        status=Status.PENDING.value,
                        command_json=list(execution.command_json),
                        cwd=execution.cwd,
                    ))
            run.status = Status.PENDING.value
            run.attempt += 1
            run.error = None
            run.finished_at = None
            run.heartbeat_at = utcnow()
            self._audit(session, run.id, "resumed", previous, Status.PENDING.value)
        self._write_manifest(run_id)
        return self.get_run(run_id)

    def retry_run(self, run_id: str) -> Run:
        original = self.get_run(run_id)
        if original.status not in {Status.FAILED.value, Status.CANCELLED.value, Status.INTERRUPTED.value}:
            raise InvalidTransition("only failed, cancelled, or interrupted runs can be retried")
        return self.create_run(
            original.workflow_id,
            original.dataset_id,
            parameters=original.parameters_json,
            parent_run_id=original.id,
        )

    def retry_step(self, run_id: str, step_key: str) -> Run:
        """Retry one failed/cancelled/interrupted step in place.

        Allowed only when the run itself is not active and every step earlier
        in sequence already succeeded or was skipped, so retrying respects the
        workflow's linear dependency order instead of re-running everything.
        """
        with self.store.session() as session:
            run = session.scalar(
                select(Run).options(selectinload(Run.step_executions)).where(Run.id == run_id)
            )
            if run is None:
                raise RuntimeNotFound(f"run {run_id!r} not found")
            if run.status not in {Status.FAILED.value, Status.CANCELLED.value, Status.INTERRUPTED.value}:
                raise InvalidTransition(
                    "only a failed, cancelled, or interrupted run allows step retry"
                )
            latest: dict[str, StepExecution] = {}
            for execution in run.step_executions:
                candidate = latest.get(execution.step_definition_id)
                if candidate is None or execution.attempt > candidate.attempt:
                    latest[execution.step_definition_id] = execution
            ordered = sorted(latest.values(), key=lambda item: item.sequence)
            target = next(
                (item for item in ordered if item.step_definition.key == step_key), None
            )
            if target is None:
                raise RuntimeNotFound(f"step {step_key!r} not found in run {run_id!r}")
            if target.status not in {Status.FAILED.value, Status.CANCELLED.value, Status.INTERRUPTED.value}:
                raise InvalidTransition(
                    f"step {step_key!r} is in status {target.status!r}; only a failed, "
                    "cancelled, or interrupted step can be retried"
                )
            blocking = [
                item
                for item in ordered
                if item.sequence < target.sequence
                and item.status not in {Status.SUCCEEDED.value, Status.SKIPPED.value}
            ]
            if blocking:
                raise InvalidTransition(
                    "earlier steps must succeed or be skipped before this step can be retried: "
                    + ", ".join(item.step_definition.key for item in blocking)
                )
            previous = run.status
            session.add(StepExecution(
                run_id=run.id,
                step_definition_id=target.step_definition_id,
                sequence=target.sequence,
                attempt=target.attempt + 1,
                status=Status.PENDING.value,
                command_json=list(target.command_json),
                cwd=target.cwd,
            ))
            run.status = Status.PENDING.value
            run.attempt += 1
            run.error = None
            run.finished_at = None
            run.heartbeat_at = utcnow()
            self._audit(
                session,
                run.id,
                "step_retry_requested",
                previous,
                Status.PENDING.value,
                {"step_key": step_key},
            )
        self._write_manifest(run_id)
        return self.get_run(run_id)

    def run_logs(self, run_id: str) -> list[dict[str, Any]]:
        """Return live/completed logs for every step execution in a run.

        Prefers an on-disk ``log_path`` (live worker output) and falls back to
        the persisted ``stdout``/``stderr`` captured at completion.
        """
        run = self.get_run(run_id)
        entries: list[dict[str, Any]] = []
        for execution in run.step_executions:
            entries.append(self.step_logs(execution.id))
        return entries

    def step_logs(self, execution_id: str) -> dict[str, Any]:
        execution = self.get_execution(execution_id)
        content: str | None = None
        if execution.log_path:
            path = Path(execution.log_path)
            if path.is_file():
                content = path.read_text(encoding="utf-8", errors="replace")
        if content is None:
            parts = [part for part in (execution.stdout, execution.stderr) if part]
            content = "\n".join(parts) if parts else None
        return {
            "step_execution_id": execution.id,
            "step_key": execution.step_definition.key,
            "status": execution.status,
            "log_path": execution.log_path,
            "content": content,
        }

    def export_datasets_csv(self, project_id: str, path: str | Path) -> Path:
        destination = Path(path).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        with temporary.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=["external_id", "name", "uri", "metadata"])
            writer.writeheader()
            for dataset in self.list_datasets(project_id):
                writer.writerow({
                    "external_id": dataset.external_id,
                    "name": dataset.name or "",
                    "uri": dataset.uri or "",
                    "metadata": json.dumps(dataset.metadata_json, sort_keys=True, separators=(",", ":")),
                })
        os.replace(temporary, destination)
        return destination

    def import_datasets_csv(self, project_id: str, path: str | Path) -> list[Dataset]:
        source = Path(path).expanduser().resolve()
        with source.open(newline="", encoding="utf-8-sig") as stream:
            rows = list(csv.DictReader(stream))
        if not rows or "external_id" not in (rows[0].keys() if rows else []):
            if rows:
                raise ValueError("dataset CSV requires an external_id column")
            return []
        imported_ids: list[str] = []
        with self.store.session() as session:
            if session.get(Project, project_id) is None:
                raise RuntimeNotFound(f"project {project_id!r} not found")
            for row in rows:
                external_id = (row.get("external_id") or "").strip()
                if not external_id:
                    raise ValueError("dataset CSV contains an empty external_id")
                raw_metadata = row.get("metadata") or "{}"
                metadata = json.loads(raw_metadata)
                if not isinstance(metadata, dict):
                    raise ValueError("dataset metadata must be a JSON object")
                dataset = session.scalar(select(Dataset).where(
                    Dataset.project_id == project_id, Dataset.external_id == external_id
                ))
                if dataset is None:
                    dataset = Dataset(project_id=project_id, external_id=external_id)
                    session.add(dataset)
                dataset.name = row.get("name") or None
                dataset.uri = row.get("uri") or None
                dataset.metadata_json = metadata
                session.flush()
                self._audit_entity(session, "dataset", dataset.id, "csv_imported")
                imported_ids.append(dataset.id)
        with self.store.session() as session:
            return list(session.scalars(select(Dataset).where(Dataset.id.in_(imported_ids)).order_by(Dataset.external_id)))

    def load_manifest(self, working_directory: str | Path) -> dict[str, Any]:
        root = Path(working_directory)
        path = root if root.name == "run.json" else root / ".alfrd" / "run.json"
        with path.open(encoding="utf-8") as stream:
            manifest = json.load(stream)
        if manifest.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"unsupported run manifest schema {manifest.get('schema_version')!r}")
        return manifest

    def recover_manifest(self, working_directory: str | Path) -> Run:
        manifest = self.load_manifest(working_directory)
        supplied_path = Path(working_directory).expanduser().resolve()
        manifest_workdir = supplied_path.parent.parent if supplied_path.name == "run.json" else supplied_path
        run_id = manifest["run_id"]
        try:
            return self.get_run(run_id)
        except RuntimeNotFound:
            pass
        with self.store.session() as session:
            workflow = session.scalar(select(WorkflowDefinition).options(
                selectinload(WorkflowDefinition.steps)
            ).where(WorkflowDefinition.id == manifest["workflow_id"]))
            dataset = session.get(Dataset, manifest["dataset_id"])
            if workflow is None or dataset is None:
                raise RuntimeNotFound("manifest workflow or dataset is not present in the database")
            definitions = {step.id: step for step in workflow.steps}
            run = Run(
                id=run_id,
                workflow_id=workflow.id,
                dataset_id=dataset.id,
                parent_run_id=manifest.get("parent_run_id"),
                status=manifest["status"],
                attempt=int(manifest.get("attempt", 1)),
                working_directory=str(manifest_workdir),
                parameters_json=manifest.get("parameters", {}),
                error=manifest.get("error"),
                pid=manifest.get("pid"),
                hostname=manifest.get("hostname"),
            )
            for item in manifest.get("steps", []):
                if item["step_definition_id"] not in definitions:
                    raise RuntimeNotFound("manifest references an unknown step definition")
                run.step_executions.append(StepExecution(
                    id=item["id"],
                    step_definition_id=item["step_definition_id"],
                    sequence=int(item["sequence"]),
                    attempt=int(item["attempt"]),
                    status=item["status"],
                    command_json=item["command"],
                    cwd=item["cwd"],
                    exit_code=item.get("exit_code"),
                    stdout=item.get("stdout"),
                    stderr=item.get("stderr"),
                    error=item.get("error"),
                    log_path=item.get("log_path"),
                ))
            for item in manifest.get("artifacts", []):
                run.artifacts.append(Artifact(
                    id=item["id"],
                    dataset_id=dataset.id,
                    step_execution_id=item.get("step_execution_id"),
                    name=item["name"],
                    path=item["path"],
                    media_type=item.get("media_type"),
                    size_bytes=int(item["size_bytes"]),
                    sha256=item["sha256"],
                    metadata_json=item.get("metadata", {}),
                ))
            session.add(run)
            session.flush()
            self._audit(session, run.id, "manifest_recovered", to_status=run.status)
        self._write_manifest(run_id)
        return self.get_run(run_id)

    def _write_manifest(self, run_id: str) -> None:
        run = self.get_run(run_id)
        document = {
            "schema_version": SCHEMA_VERSION,
            "run_id": run.id,
            "workflow_id": run.workflow_id,
            "dataset_id": run.dataset_id,
            "parent_run_id": run.parent_run_id,
            "status": run.status,
            "attempt": run.attempt,
            "working_directory": run.working_directory,
            "parameters": run.parameters_json,
            "error": run.error,
            "created_at": _iso(run.created_at),
            "started_at": _iso(run.started_at),
            "finished_at": _iso(run.finished_at),
            "heartbeat_at": _iso(run.heartbeat_at),
            "pid": run.pid,
            "hostname": run.hostname,
            "steps": [{
                "id": item.id,
                "step_definition_id": item.step_definition_id,
                "key": item.step_definition.key,
                "sequence": item.sequence,
                "attempt": item.attempt,
                "status": item.status,
                "command": item.command_json,
                "cwd": item.cwd,
                "exit_code": item.exit_code,
                "stdout": item.stdout,
                "stderr": item.stderr,
                "error": item.error,
                "log_path": item.log_path,
            } for item in run.step_executions],
            "artifacts": [{
                "id": item.id,
                "dataset_id": item.dataset_id,
                "step_execution_id": item.step_execution_id,
                "name": item.name,
                "path": item.path,
                "media_type": item.media_type,
                "size_bytes": item.size_bytes,
                "sha256": item.sha256,
                "metadata": item.metadata_json,
            } for item in run.artifacts],
        }
        directory = Path(run.working_directory) / ".alfrd"
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / "run.json"
        temporary = directory / "run.json.tmp"
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(document, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)

    @staticmethod
    def _audit(
        session,
        run_id: str,
        action: str,
        from_status: str | None = None,
        to_status: str | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        session.add(AuditEvent(
            aggregate_type="run",
            aggregate_id=run_id,
            action=action,
            from_status=from_status,
            to_status=to_status,
            payload_json=dict(payload or {}),
        ))

    @staticmethod
    def _audit_entity(session, aggregate_type: str, aggregate_id: str, action: str) -> None:
        session.add(AuditEvent(
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            action=action,
        ))
