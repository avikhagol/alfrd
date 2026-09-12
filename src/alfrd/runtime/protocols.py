from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from .models import Artifact, Dataset, Run, StepExecution, WorkflowDefinition
from .service import Status


@runtime_checkable
class RuntimeOperations(Protocol):
    """Minimal persistence/service contract required by future orchestrators."""

    def create_run(
        self,
        workflow_id: str,
        dataset_id: str,
        *,
        parameters: Mapping[str, Any] | None = None,
        parent_run_id: str | None = None,
        run_id: str | None = None,
        working_directory: str | Path | None = None,
    ) -> Run: ...

    def get_run(self, run_id: str) -> Run: ...

    def get_workflow(self, workflow_id: str) -> WorkflowDefinition: ...

    def get_dataset(self, dataset_id: str) -> Dataset: ...

    def next_pending_execution(self, run_id: str) -> StepExecution | None: ...

    def transition_run(self, run_id: str, status: Status | str, *, error: str | None = None) -> Run: ...

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
    ) -> StepExecution: ...

    def attach_process(self, run_id: str, *, pid: int, hostname: str | None = None) -> Run: ...

    def record_artifact(
        self,
        run_id: str,
        path: str | Path,
        *,
        step_execution_id: str | None = None,
        name: str | None = None,
        media_type: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Artifact: ...


@runtime_checkable
class StepWorker(Protocol):
    """Executes one already selected step; it does not orchestrate a workflow."""

    def execute(
        self,
        execution_id: str,
        *,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> StepExecution: ...
