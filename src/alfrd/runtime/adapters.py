"""Adapters between typed pipeline events and durable runtime state."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from alfrd.core.pipeline import (
    ArtifactRef,
    BatchResult,
    BatchResultAdapter,
    DatasetStarted,
    EventSink,
    ExecutionEvent,
    PipelineContext,
    PipelineCore,
    PipelineStepBase,
    RunFinished,
    RunStarted,
    StepFailed,
    StepSkipped,
    StepStarted,
    StepSucceeded,
)

from .models import Artifact, StepExecution
from .protocols import RuntimeOperations
from .service import Status


class CompositeEventSink:
    """Fan out an event to ordered sinks, preserving failure semantics."""

    def __init__(self, *sinks: EventSink) -> None:
        self.sinks = tuple(sinks)

    def __call__(self, event: ExecutionEvent) -> None:
        for sink in self.sinks:
            sink(event)


def artifact_ref_from_model(artifact: Artifact) -> ArtifactRef:
    """Adapt one persisted artifact to the canonical pipeline representation."""

    metadata = dict(artifact.metadata_json or {})
    kind = str(metadata.pop("kind", "file"))
    description = str(metadata.pop("description", ""))
    return ArtifactRef(
        artifact.path,
        kind=kind,
        description=description,
        metadata=metadata,
        media_type=artifact.media_type,
    )


class RuntimeEventSink:
    """Persist one single-dataset ``PipelineCore`` run from lifecycle events."""

    def __init__(self, service: RuntimeOperations, run_id: str) -> None:
        self.service = service
        self.run_id = run_id
        self.run = service.get_run(run_id)
        self.dataset = service.get_dataset(self.run.dataset_id)
        self._active: dict[str, str] = {}

    def __call__(self, event: ExecutionEvent) -> None:
        if event.run_id != self.run_id:
            raise ValueError(
                f"event run {event.run_id!r} does not match runtime run {self.run_id!r}"
            )
        if isinstance(event, RunStarted):
            if event.dataset_count != 1:
                raise ValueError("a persisted runtime run executes exactly one dataset")
            self.service.transition_run(self.run_id, Status.RUNNING)
        elif isinstance(event, DatasetStarted):
            if event.dataset_id != self.dataset.external_id:
                raise ValueError(
                    f"pipeline dataset {event.dataset_id!r} does not match "
                    f"runtime dataset {self.dataset.external_id!r}"
                )
        elif isinstance(event, StepStarted):
            execution = self._execution(event.step_name, Status.PENDING.value)
            self.service.transition_execution(execution.id, Status.RUNNING)
            self._active[event.step_name] = execution.id
        elif isinstance(event, (StepSucceeded, StepFailed)):
            execution_id = self._active.pop(event.step_name)
            result = event.result
            if result is not None:
                self._record_artifacts(result.artifacts, execution_id)
            if isinstance(event, StepSucceeded):
                self.service.transition_execution(execution_id, Status.SUCCEEDED, exit_code=0)
            else:
                error = result.error.message if result is not None and result.error else None
                self.service.transition_execution(
                    execution_id, Status.FAILED, exit_code=1, error=error
                )
        elif isinstance(event, StepSkipped):
            execution = self._execution(event.step_name, Status.PENDING.value)
            self.service.transition_execution(
                execution.id, Status.SKIPPED, error=event.reason
            )
        elif isinstance(event, RunFinished):
            if event.result is not None:
                self._record_artifacts(event.result.artifacts)
            error = None
            if not event.success and event.result is not None:
                failed = next(
                    (step for step in event.result.step_results if step.error is not None),
                    None,
                )
                error = (
                    failed.error.message
                    if failed is not None and failed.error is not None
                    else "pipeline failed"
                )
            self.service.transition_run(
                self.run_id,
                Status.SUCCEEDED if event.success else Status.FAILED,
                error=error,
            )

    def _execution(self, step_name: str, status: str) -> StepExecution:
        matches = [
            item
            for item in self.service.get_run(self.run_id).step_executions
            if item.step_definition.key == step_name and item.status == status
        ]
        if len(matches) != 1:
            raise LookupError(
                f"expected one {status!r} execution for step {step_name!r}; "
                f"found {len(matches)}"
            )
        return matches[0]

    def _record_artifacts(
        self, artifacts: Iterable[ArtifactRef], execution_id: str | None = None
    ) -> None:
        for artifact in artifacts:
            metadata = dict(artifact.metadata)
            metadata.setdefault("kind", artifact.kind)
            if artifact.description:
                metadata.setdefault("description", artifact.description)
            self.service.record_artifact(
                self.run_id,
                artifact.path,
                step_execution_id=execution_id,
                media_type=artifact.media_type,
                metadata=metadata,
            )


class RuntimePipelineRunner:
    """Run typed steps against one persisted workflow and dataset."""

    def __init__(self, service: RuntimeOperations) -> None:
        self.service = service

    def run(
        self,
        workflow_id: str,
        dataset_id: str,
        steps: Sequence[PipelineStepBase],
        *,
        params: Mapping[str, Any] | None = None,
        context: PipelineContext | Mapping[str, Any] | None = None,
        result_adapter: BatchResultAdapter | Any | None = None,
        event_sinks: Iterable[EventSink] = (),
    ) -> BatchResult:
        workflow = self.service.get_workflow(workflow_id)
        expected = [item.key for item in workflow.steps]
        actual = [item.name for item in steps]
        if actual != expected:
            raise ValueError(
                f"typed step order {actual!r} does not match persisted workflow {expected!r}"
            )

        dataset = self.service.get_dataset(dataset_id)
        run = self.service.create_run(
            workflow_id,
            dataset_id,
            parameters=params,
        )
        runtime_sink = RuntimeEventSink(self.service, run.id)
        # Projection sinks run first so their failure can still be represented
        # as a failed active execution by the durable sink.
        sink = CompositeEventSink(*tuple(event_sinks), runtime_sink)
        values = dict(dataset.metadata_json or {})
        values.update(
            {
                "dataset_id": dataset.external_id,
                "dataset_record_id": dataset.id,
                "dataset_name": dataset.name,
                "dataset_uri": dataset.uri,
            }
        )
        pipeline = PipelineCore(
            steps,
            context=context,
            config=workflow.parameters_json,
            event_sink=sink,
            result_adapter=result_adapter,
        )
        return pipeline.run([values], params=params, run_id=run.id)


__all__ = [
    "CompositeEventSink",
    "RuntimeEventSink",
    "RuntimePipelineRunner",
    "artifact_ref_from_model",
]
