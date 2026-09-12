"""AVICA compatibility contract fixture tests (roadmap workstream 0.2.9.0).

These tests prove, using only public ALFRD APIs, that ALFRD's typed pipeline
engine, LogFrame table adapter, and configuration primitives can host an
AVICA-shaped pipeline:

- no-argument step/validator construction,
- class-based validators wired through ``validate_by``,
- AVICA-compatible parameter precedence,
- structured ``StepResult``/``BatchResult`` returns,
- result-CSV and crash-snapshot hooks,
- multi-dataset execution sharing one ``LogFrame`` table,
- manifest-declared path/glob artifact discovery,
- an optional domain inspector returning generic metadata (no CASA/AVICA).
"""

from __future__ import annotations

import csv
import fnmatch
import json
from pathlib import Path

import pandas as pd
import pytest

from alfrd.core.logframe import LogFrame, LogFrameEventSink
from alfrd.core.pipeline import (
    CrashSnapshotAdapter,
    PipelineContext,
    PipelineStepBase,
    PipelineStepValidatorBase,
    ResultCSVAdapter,
    StepFailed,
    StepSucceeded,
)
from alfrd.manifest import load_manifest, validate_manifest
from alfrd.config import BaseConfig, Config

from tests.avica_compat.aliases import (
    AvicaPipelineCore,
    AvicaResult,
    BaseConfig as AliasBaseConfig,
    CONFIG_MAPPING as ALIAS_CONFIG_MAPPING,
    Config as AliasConfig,
    LogFramework,
)
from tests.avica_compat.pipeline import (
    ALL_STEPS,
    Calibrate,
    InputExists,
    Prepare,
    Summarize,
    Transform,
    build_pipeline,
)

FIXTURE_DIR = Path(__file__).parent
MANIFEST_PATH = FIXTURE_DIR / "alfrd.yaml"


# ---------------------------------------------------------------------------
# No-argument construction and class-based validators
# ---------------------------------------------------------------------------


def test_steps_and_validators_construct_with_no_arguments():
    for step_cls in ALL_STEPS:
        instance = step_cls()
        assert isinstance(instance, PipelineStepBase)
        assert instance.name

    validator = InputExists()
    assert isinstance(validator, PipelineStepValidatorBase)
    assert validator.run_after is False


def test_class_based_validator_is_registered_via_validate_by():
    step = Prepare()
    assert len(step.before) == 1
    assert isinstance(step.before[0], InputExists)
    assert step.after == ()

    transform_step = Transform()
    assert len(transform_step.after) == 1
    assert transform_step.after[0].run_after is True


# ---------------------------------------------------------------------------
# AVICA-compatible parameter precedence
# ---------------------------------------------------------------------------


def test_parameter_precedence_step_qualified_beats_global_beats_context_beats_config(
    tmp_path: Path,
):
    """precedence: step-qualified explicit > global explicit > context >
    merged config > callable default."""

    class Echo(PipelineStepBase):
        name = "echo"

        def run(self, **params):
            return params.get("value", "callable-default")

    core = build_pipeline(
        config={"value": "config-value"},
        context=PipelineContext({"value": "context-value"}),
    )
    core.register_step(Echo)

    # 1. callable default wins when nothing else supplies a value.
    bare = AvicaPipelineCore([Echo])
    result = bare.run({"dataset_id": "d0"})
    assert result.datasets[0].steps[0].value == "callable-default"

    # 2. merged config used when no context/global/qualified value given.
    config_only = AvicaPipelineCore([Echo], config={"value": "config-value"})
    result = config_only.run({"dataset_id": "d1"})
    assert result.datasets[0].steps[0].value == "config-value"

    # 3. context beats config.
    context_over_config = AvicaPipelineCore(
        [Echo],
        config={"value": "config-value"},
        context=PipelineContext({"value": "context-value"}),
    )
    result = context_over_config.run({"dataset_id": "d2"})
    assert result.datasets[0].steps[0].value == "context-value"

    # 4. global explicit beats context.
    result = context_over_config.run({"dataset_id": "d3"}, params={"value": "global-value"})
    assert result.datasets[0].steps[0].value == "global-value"

    # 5. step-qualified explicit beats global explicit.
    result = context_over_config.run(
        {"dataset_id": "d4"},
        params={"value": "global-value", "echo.value": "qualified-value"},
    )
    assert result.datasets[0].steps[0].value == "qualified-value"


def test_get_kwargs_resolves_avica_shaped_step_qualified_defaults():
    core = build_pipeline(provided_pipe_params={"transform.transform_solint": "80s"})
    transform_step = next(step for step in core.steps if step.name == "transform")
    kwargs = core.get_kwargs(transform_step)
    assert kwargs["transform_solint"] == "80s"


# ---------------------------------------------------------------------------
# Structured StepResult / BatchResult behavior
# ---------------------------------------------------------------------------


def test_structured_step_and_batch_result_for_single_dataset(tmp_path: Path):
    workdir = tmp_path / "target-a"
    core = build_pipeline()
    result = core.run(
        {"dataset_id": "target-a"},
        params={
            "input_file": str(_touch(tmp_path / "input.dat")),
            "workdir": str(workdir),
            "target_name": "target-a",
        },
    )

    assert isinstance(result, AvicaResult)
    assert result.success
    assert result.success_count == 1
    assert result.failed_count == 0
    step_names = [step.step_name for step in result.step_results]
    assert step_names == ["prepare", "transform", "calibrate", "summarize"]
    assert all(step.success for step in result.step_results)
    assert (workdir / "target-a_result.csv").exists()


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("data\n")
    return path


# ---------------------------------------------------------------------------
# Result-CSV and crash-snapshot hooks
# ---------------------------------------------------------------------------


def test_result_csv_and_crash_snapshot_hooks(tmp_path: Path):
    csv_path = tmp_path / "results.csv"
    crash_path = tmp_path / "avica_crash.json"

    class Explode(PipelineStepBase):
        name = "explode"

        def run(self, **params):
            raise RuntimeError(f"boom for {params.get('target_name')}")

    core = AvicaPipelineCore(
        [Explode],
        result_adapter=ResultCSVAdapter(csv_path),
        crash_adapter=CrashSnapshotAdapter(crash_path),
    )
    result = core.run({"dataset_id": "broken", "target_name": "broken"})

    assert not result.success
    with csv_path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert rows[0]["dataset_id"] == "broken"
    assert rows[0]["status"] == "failed"

    snapshot = json.loads(crash_path.read_text(encoding="utf-8"))
    assert snapshot["dataset_id"] == "broken"
    assert snapshot["error"]["type"] == "RuntimeError"


# ---------------------------------------------------------------------------
# Multi-dataset execution sharing one LogFrame/table adapter
# ---------------------------------------------------------------------------


def test_multi_dataset_execution_shares_one_logframe(tmp_path: Path):
    frame = pd.DataFrame({"TARGET_NAME": ["alpha", "beta"]})
    logframe = LogFrame(frame, primary_colname="TARGET_NAME")
    sink = LogFrameEventSink(logframe)

    core = build_pipeline(event_sink=sink)
    datasets = [
        {
            "dataset_id": "alpha",
            "target_name": "alpha",
            "input_file": str(_touch(tmp_path / "alpha" / "input.dat")),
            "workdir": str(tmp_path / "alpha"),
        },
        {
            "dataset_id": "beta",
            "target_name": "beta",
            "input_file": str(_touch(tmp_path / "beta" / "input.dat")),
            "workdir": str(tmp_path / "beta"),
        },
    ]

    result = core.run(datasets)

    assert result.success
    assert len(result.datasets) == 2
    for step_name in ("prepare", "transform", "calibrate", "summarize"):
        assert logframe.get_value(step_name, primary_value="alpha") == "succeeded"
        assert logframe.get_value(step_name, primary_value="beta") == "succeeded"


def test_multi_dataset_partial_failure_is_recorded_per_dataset(tmp_path: Path):
    frame = pd.DataFrame({"TARGET_NAME": ["good", "bad"]})
    logframe = LogFrame(frame, primary_colname="TARGET_NAME")
    sink = LogFrameEventSink(logframe)

    class MaybeFail(PipelineStepBase):
        name = "maybe_fail"

        def run(self, **params):
            if params.get("target_name") == "bad":
                raise RuntimeError("bad dataset")

    core = AvicaPipelineCore([MaybeFail], event_sink=sink)
    result = core.run(
        [
            {"dataset_id": "good", "target_name": "good"},
            {"dataset_id": "bad", "target_name": "bad"},
        ]
    )

    assert not result.success
    assert logframe.get_value("maybe_fail", primary_value="good") == "succeeded"
    assert logframe.get_value("maybe_fail", primary_value="bad") == "failed"


# ---------------------------------------------------------------------------
# Documented downstream aliases resolve as specified in the roadmap
# ---------------------------------------------------------------------------


def test_documented_avica_aliases_resolve_to_alfrd_public_names():
    from alfrd.core.logframe import LogFrame
    from alfrd.core.pipeline import BatchResult, PipelineCore

    assert AvicaPipelineCore is PipelineCore
    assert AvicaResult is BatchResult
    assert LogFramework is LogFrame
    assert AliasBaseConfig is BaseConfig
    assert AliasConfig is Config
    assert ALIAS_CONFIG_MAPPING is not None  # importable and usable as a dict


# ---------------------------------------------------------------------------
# AVICA-shaped alfrd.yaml manifest fixture
# ---------------------------------------------------------------------------


def test_avica_shaped_manifest_validates_without_importing_project_code():
    manifest = load_manifest(MANIFEST_PATH)
    assert manifest.name == "avica-compat-fixture"
    assert manifest.get_entrypoint("runner").cmd == ("fixture", "pipe", "run")
    assert manifest.get_entrypoint("config").cmd == ("fixture", "pipe", "config")
    assert [definition.name for definition in manifest.schema] == ["steps"]
    assert [artifact.name for artifact in manifest.artifacts] == [
        "result_table",
        "pipeline_log",
        "diagnostic_plots",
        "prepared_data",
    ]


def test_avica_shaped_manifest_matches_the_real_avica_field_shape():
    """The fixture must preserve AVICA's existing top-level manifest shape."""
    import yaml

    raw = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    validate_manifest(raw)
    # AVICA's real alfrd.yaml (read-only reference) uses these same keys.
    assert set(raw) >= {"name", "entrypoint", "schema"}
    assert raw["entrypoint"][0]["cmd"][0:2] == ["fixture", "pipe"]


def test_manifest_declared_artifacts_are_discoverable_after_a_run(tmp_path: Path):
    """Verify path/glob artifact discovery for AVICA-shaped outputs."""
    import yaml

    manifest = load_manifest(MANIFEST_PATH)
    workdir = tmp_path / "wd_target-a"
    core = build_pipeline()
    result = core.run(
        {"dataset_id": "target-a"},
        params={
            "input_file": str(_touch(tmp_path / "input.dat")),
            "workdir": str(workdir),
            "target_name": "target-a",
        },
    )
    assert result.success

    dataset = {"WORKDIR": str(workdir), "TARGET_NAME": "target-a"}
    discovered = {}
    for artifact in manifest.artifacts:
        pattern = artifact.path_pattern.format(dataset=_DatasetView(dataset))
        if "*" in pattern:
            parent = Path(pattern).parent
            glob_name = Path(pattern).name
            matches = (
                [str(p) for p in parent.glob(glob_name)] if parent.exists() else []
            )
            discovered[artifact.name] = matches
        else:
            discovered[artifact.name] = [pattern] if Path(pattern).exists() else []

    assert discovered["result_table"] == [str(workdir / "target-a_result.csv")]
    assert discovered["pipeline_log"] == [str(workdir / "calibration.log")]
    assert len(discovered["diagnostic_plots"]) == 1
    assert fnmatch.fnmatch(discovered["diagnostic_plots"][0], "*target-a_snr.png")
    assert discovered["prepared_data"] == [str(workdir / "prepared.json")]


class _DatasetView:
    """Minimal attribute-style accessor for ``{dataset.FIELD}`` manifest patterns."""

    def __init__(self, values: dict) -> None:
        self._values = values

    def __getattr__(self, name: str):
        try:
            return self._values[name]
        except KeyError as exc:  # pragma: no cover - defensive
            raise AttributeError(name) from exc


# ---------------------------------------------------------------------------
# Optional domain inspector returning generic metadata (no CASA/AVICA import)
# ---------------------------------------------------------------------------


def test_optional_domain_inspector_returns_generic_metadata_only(tmp_path: Path):
    """An AVICA-style inspector may report structured metadata about an
    artifact (e.g. a directory standing in for a Measurement Set) without
    ALFRD importing CASA or AVICA to produce it."""

    def inspect_result_table(path: Path) -> dict:
        with path.open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        return {"kind": "table", "row_count": len(rows), "columns": list(rows[0].keys()) if rows else []}

    core = build_pipeline()
    workdir = tmp_path / "target-a"
    result = core.run(
        {"dataset_id": "target-a"},
        params={
            "input_file": str(_touch(tmp_path / "input.dat")),
            "workdir": str(workdir),
            "target_name": "target-a",
        },
    )
    table_artifact = next(
        artifact
        for step in result.datasets[0].steps
        for artifact in step.artifacts
        if artifact.kind == "table"
    )
    metadata = inspect_result_table(table_artifact.path)
    assert metadata == {"kind": "table", "row_count": 1, "columns": ["TARGET_NAME", "STATUS"]}
    # Nothing in this module imports the real avica package or CASA bindings.
    import sys

    assert not any(
        name == "avica" or name.startswith("avica.") or name.startswith(("casatools", "casacore"))
        for name in sys.modules
    )
