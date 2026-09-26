"""Domain-neutral, AVICA-shaped pipeline built only from public ALFRD APIs.

The steps below are deliberately generic (no CASA/FITS/MS/rPICARD names or
behavior) but reproduce the *contract shape* AVICA's real
``avica.pipe.core`` module relies on:

- steps and validators constructed with no arguments (``StepCls()``),
- class-based validators wired through ``validate_by``,
- AVICA-compatible parameter precedence (step-qualified explicit > global
  explicit > context > merged config > callable default), exercised via
  ``PipelineCore.resolve_parameters``/``get_kwargs``,
- structured ``StepResult``/``BatchResult`` returns (steps return artifacts;
  ``PipelineCore`` normalizes them into a ``StepResult``),
- crash-snapshot and result-CSV adapter hooks,
- multi-dataset execution sharing one ``LogFrame`` table adapter via an
  event sink.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from alfrd.core.pipeline import (
    ArtifactRef,
    PipelineCore,
    PipelineStepBase,
    PipelineStepValidatorBase,
    PipelineStepValidatorResult,
    StepResult,
)


class InputExists(PipelineStepValidatorBase):
    """AVICA-shaped pre-step validator: no-argument construction, class-based."""

    name = "input_exists"
    run_after = False

    def run(self, **params: Any) -> PipelineStepValidatorResult:
        input_file = params["input_file"]
        exists = Path(input_file).exists()
        return PipelineStepValidatorResult(
            success=exists,
            description="" if exists else f"Input not found: {input_file}",
        )


class ResultRecorded(PipelineStepValidatorBase):
    """AVICA-shaped post-step validator (``run_after = True``, no-arg ctor)."""

    name = "result_recorded"
    run_after = True

    def run(self, **params: Any) -> PipelineStepValidatorResult:
        return PipelineStepValidatorResult(success=params.get("result") is not None)


class Prepare(PipelineStepBase):
    """Mirrors AVICA's ``preprocess_fitsidi``-shaped first step."""

    name = "prepare"
    description = "Prepare input dataset"
    validate_by = (InputExists,)

    def run(self, **params: Any) -> list[ArtifactRef]:
        workdir = params["workdir"]
        prepare_flag = params.get("prepare_flag", False)
        output = Path(workdir) / "prepared.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(f'{{"prepared": true, "flag": {str(bool(prepare_flag)).lower()}}}\n')
        return [ArtifactRef(output, kind="json", description="Prepared data")]


class Transform(PipelineStepBase):
    """Mirrors AVICA's ``fits_to_ms``-shaped conversion step."""

    name = "transform"
    description = "Transform prepared input"
    validate_by = (ResultRecorded,)

    def run(self, **params: Any) -> StepResult:
        workdir = params["workdir"]
        transform_solint = params.get("transform_solint", "inf")
        output = Path(workdir) / "transformed.txt"
        output.write_text(f"solint={transform_solint}\n")
        return StepResult(
            dataset_id="",
            step_name=self.name,
            status="succeeded",
            value=transform_solint,
            artifacts=[ArtifactRef(output, kind="file", description="Transformed dataset")],
        )


class Calibrate(PipelineStepBase):
    """Mirrors AVICA's ``rpicard``-shaped calibration step producing plots."""

    name = "calibrate"
    description = "Calibrate dataset"

    def run(self, **params: Any) -> list[ArtifactRef]:
        workdir = params["workdir"]
        target_name = params["target_name"]
        plot_dir = Path(workdir) / "plots"
        plot_dir.mkdir(parents=True, exist_ok=True)
        plot = plot_dir / f"{target_name}_snr.png"
        plot.write_bytes(b"")
        log = Path(workdir) / "calibration.log"
        log.write_text(f"Calibration completed for {target_name}\n")
        return [
            ArtifactRef(log, kind="log", description="Calibration log"),
            ArtifactRef(plot, kind="image", description="Diagnostic plot"),
        ]


class Summarize(PipelineStepBase):
    """Mirrors AVICA's final result-table-writing step."""

    name = "summarize"
    description = "Summarize dataset outcome"

    def run(self, **params: Any) -> list[ArtifactRef]:
        workdir = params["workdir"]
        target_name = params["target_name"]
        summary = Path(workdir) / f"{target_name}_result.csv"
        summary.write_text(f"TARGET_NAME,STATUS\n{target_name},complete\n")
        return [ArtifactRef(summary, kind="table", description="Result table")]


ALL_STEPS: tuple[type[PipelineStepBase], ...] = (Prepare, Transform, Calibrate, Summarize)


def build_pipeline(*, config: dict[str, Any] | None = None, **kwargs: Any) -> PipelineCore:
    """Construct the fixture pipeline from public ALFRD APIs only.

    ``config`` follows merged-configuration precedence (rank 4 of 5);
    ``kwargs`` are forwarded to ``PipelineCore`` (context, event_sink,
    crash_adapter, result_adapter, provided_pipe_params, ...).
    """

    return PipelineCore(list(ALL_STEPS), config=config, **kwargs)


__all__ = [
    "ALL_STEPS",
    "Calibrate",
    "InputExists",
    "Prepare",
    "ResultRecorded",
    "Summarize",
    "Transform",
    "build_pipeline",
]
