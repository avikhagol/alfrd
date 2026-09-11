from __future__ import annotations

import csv
import json
from pathlib import Path

from alfrd.core.pipeline import (
    ArtifactRef,
    BatchResult,
    CrashSnapshotAdapter,
    DatasetFinished,
    DatasetStarted,
    PipelineContext,
    PipelineCore,
    PipelineStepBase,
    PipelineStepValidatorBase,
    PipelineStepValidatorResult,
    ResultCSVAdapter,
    RunFinished,
    RunStarted,
    StepFailed,
    StepResult,
    StepSkipped,
    StepStarted,
    StepSucceeded,
)


class CallableStep(PipelineStepBase):
    def __init__(self, name, function, *, before=(), after=()):
        super().__init__(name=name, description=f"run {name}", before=before, after=after)
        self.function = function

    def execute(self, **params):
        return self.function(**params)


class CallableValidator(PipelineStepValidatorBase):
    def __init__(self, name, function, *, run_once=False):
        super().__init__(name=name, run_once=run_once)
        self.function = function

    def validate(self, **params):
        return self.function(**params)


def test_parameter_precedence_and_instance_context_isolation():
    observed = []

    def capture(value="default"):
        observed.append(value)

    step = CallableStep("capture", capture)
    first = PipelineCore(
        [step],
        context=PipelineContext({"value": "context"}),
        config=[{"value": "old-config"}, {"value": "config"}],
    )
    second = PipelineCore([step], context=PipelineContext({"value": "other"}))

    result = first.run(
        [{"dataset_id": "one"}],
        params={"value": "global", "capture.value": "qualified"},
    )
    second.run([{"dataset_id": "two"}])

    assert result.success
    assert observed == ["qualified", "other"]
    assert first.context is not second.context
    assert first.resolve_parameters(step, {"value": "global"}) == {"value": "global"}


def test_validation_normalization_order_timing_and_artifacts(tmp_path: Path):
    order = []
    before = CallableValidator("before", lambda: order.append("before") or [True, None])
    after = CallableValidator(
        "after",
        lambda: order.append("after") or PipelineStepValidatorResult(True, "valid"),
    )

    def execute():
        order.append("step")
        return [ArtifactRef(tmp_path / "product.fits", kind="fits")]

    events = []
    result = PipelineCore(
        [CallableStep("image", execute, before=[before], after=[after])],
        event_sink=events.append,
    ).run([{"dataset_id": "source"}])

    assert order == ["before", "step", "after"]
    assert result.success
    step_result = result.datasets[0].steps[0]
    assert step_result.started_at <= step_result.ended_at
    assert step_result.duration_seconds >= 0
    assert step_result.artifacts[0].path == tmp_path / "product.fits"
    assert [type(event) for event in events] == [
        RunStarted,
        DatasetStarted,
        StepStarted,
        StepSucceeded,
        DatasetFinished,
        RunFinished,
    ]


def test_failure_is_isolated_per_dataset_and_later_steps_are_skipped():
    calls = []

    def fragile(dataset_id):
        calls.append((dataset_id, "fragile"))
        if dataset_id == "bad":
            raise RuntimeError("broken dataset")

    def later(dataset_id):
        calls.append((dataset_id, "later"))

    events = []
    result = PipelineCore(
        [CallableStep("fragile", fragile), CallableStep("later", later)],
        event_sink=events.append,
    ).run([{"dataset_id": "bad"}, {"dataset_id": "good"}])

    assert not result.success
    assert calls == [("bad", "fragile"), ("good", "fragile"), ("good", "later")]
    assert [step.status for step in result.datasets[0].steps] == ["failed", "skipped"]
    assert result.datasets[0].steps[0].error.type == "RuntimeError"
    assert "broken dataset" in result.datasets[0].steps[0].error.message
    assert [type(event) for event in events] == [
        RunStarted,
        DatasetStarted,
        StepStarted,
        StepFailed,
        StepSkipped,
        DatasetFinished,
        DatasetStarted,
        StepStarted,
        StepSucceeded,
        StepStarted,
        StepSucceeded,
        DatasetFinished,
        RunFinished,
    ]


def test_validator_rejection_and_run_once_are_dataset_local_safe():
    calls = []
    once = CallableValidator("once", lambda: calls.append("once") or True, run_once=True)
    reject = CallableValidator("reject", lambda allowed: allowed)
    step = CallableStep("guarded", lambda dataset_id: calls.append(dataset_id), before=[once, reject])

    result = PipelineCore([step]).run(
        [
            {"dataset_id": "bad", "allowed": False},
            {"dataset_id": "good", "allowed": True},
        ]
    )

    assert calls == ["once", "good"]
    assert [dataset.success for dataset in result.datasets] == [False, True]
    assert result.datasets[0].steps[0].error.type == "ValidationError"


def test_crash_snapshot_and_result_csv_adapters(tmp_path: Path):
    crash_path = tmp_path / "crash.json"
    csv_path = tmp_path / "results.csv"

    def explode(dataset_id):
        raise KeyError(dataset_id)

    result = PipelineCore(
        [CallableStep("explode", explode)],
        crash_adapter=CrashSnapshotAdapter(crash_path),
        result_adapter=ResultCSVAdapter(csv_path),
    ).run([{"dataset_id": "broken"}])

    assert isinstance(result, BatchResult)
    snapshot = json.loads(crash_path.read_text(encoding="utf-8"))
    assert snapshot["dataset_id"] == "broken"
    assert snapshot["step_name"] == "explode"
    assert snapshot["error"]["type"] == "KeyError"
    with csv_path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert rows[0]["dataset_id"] == "broken"
    assert rows[0]["status"] == "failed"
    assert result.datasets[0].steps[0].artifacts[-1].kind == "crash-snapshot"
    assert result.artifacts[-1].kind == "result-csv"


def test_step_result_and_batch_result_serialize_stably():
    step = StepResult(dataset_id="d", step_name="s", status="succeeded", value=3)
    batch = BatchResult.from_step_results([step])

    assert batch.success
    assert batch.to_dict()["datasets"][0]["steps"][0]["value"] == 3


def test_no_argument_run_style_steps_and_validator_classes_are_adapted():
    observed = []

    class After(PipelineStepValidatorBase):
        run_after = True

        def run(self, result):
            observed.append(("after", result))
            return [True]

    class Compatible(PipelineStepBase):
        name = "compatible"
        validate_by = (After,)

        def run(self, value="callable-default"):
            observed.append(("run", value))
            return value

    core = PipelineCore(
        {"value": "config"},
        [Compatible],
        {"value": None},
        context=PipelineContext({"value": None}),
    )

    result = core.execute()

    assert result.success
    assert observed == [("run", "callable-default"), ("after", "callable-default")]
    assert core.step_names() == ["compatible"]
    assert core.get_kwargs(core.steps[0]) == {}
