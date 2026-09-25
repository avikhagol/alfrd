"""Design-only example of the proposed ALFRD 0.2.1.0 API.

The imported classes do not all exist yet. This file is an interface proposal,
not an executable example in the current implementation.
"""

from pathlib import Path

from alfrd.core.pipeline import (
    ArtifactRef,
    PipelineCore,
    PipelineStepBase,
    PipelineStepValidatorBase,
    PipelineStepValidatorResult,
    StepResult,
)


class InputExists(PipelineStepValidatorBase):
    name = "input_exists"
    run_after = False

    def run(self, input_file: str) -> PipelineStepValidatorResult:
        exists = Path(input_file).exists()
        return PipelineStepValidatorResult(
            success=[exists],
            msg="" if exists else f"Input not found: {input_file}",
        )


class Prepare(PipelineStepBase):
    name = "prepare"
    validate_by = [InputExists]

    def run(self, input_file: str, workdir: str) -> StepResult:
        output = Path(workdir) / "prepared.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text('{"prepared": true}\n')
        return StepResult.ok(
            name=self.name,
            description="Prepared input metadata",
            artifacts=[
                ArtifactRef(
                    id="prepared_data",
                    kind="json",
                    path=str(output),
                    label="Prepared data",
                )
            ],
        )


class Calibrate(PipelineStepBase):
    name = "calibrate"

    def run(self, workdir: str) -> StepResult:
        log = Path(workdir) / "calibration.log"
        log.write_text("Calibration completed\n")
        return StepResult.ok(
            name=self.name,
            description="Completed calibration",
            artifacts=[
                ArtifactRef(
                    id="calibration_log",
                    kind="log",
                    path=str(log),
                    label="Calibration log",
                )
            ],
        )


class Summarize(PipelineStepBase):
    name = "summarize"

    def run(self, target_name: str, workdir: str) -> StepResult:
        summary = Path(workdir) / "summary.csv"
        summary.write_text(f"TARGET_NAME,STATUS\n{target_name},complete\n")
        return StepResult.ok(
            name=self.name,
            description="Created summary table",
            artifacts=[
                ArtifactRef(
                    id="summary_table",
                    kind="table",
                    path=str(summary),
                    label="Summary",
                    media_type="text/csv",
                )
            ],
        )


def build_pipeline() -> PipelineCore:
    return PipelineCore(steps=[Prepare, Calibrate, Summarize])
