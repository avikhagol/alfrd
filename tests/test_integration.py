from __future__ import annotations

from pathlib import Path

import pandas as pd

from alfrd import ArtifactDefinition, ArtifactRef, LogFrame, LogFrameEventSink
from alfrd.core.pipeline import PipelineStepBase
from alfrd.gui import create_app
from alfrd.gui.services import RuntimeCatalogReader
from alfrd.manifest import load_manifest
from alfrd.runtime import (
    RuntimePipelineRunner,
    RuntimeService,
    RuntimeStore,
    artifact_ref_from_model,
)


class BuildArtifact(PipelineStepBase):
    name = "build"

    def execute(self, dataset_id: str, output_path: str) -> ArtifactRef:
        path = Path(output_path)
        path.write_text(f"built:{dataset_id}", encoding="utf-8")
        return ArtifactRef(
            path,
            kind="report",
            description="Build report",
            metadata={"dataset": dataset_id},
            media_type="text/plain",
        )


class Fail(PipelineStepBase):
    name = "fail"

    def execute(self) -> None:
        raise RuntimeError("intentional")


class NeverRuns(PipelineStepBase):
    name = "later"

    def execute(self) -> None:
        raise AssertionError("this step must be skipped")


def test_manifest_pipeline_logframe_runtime_and_web_catalog_integration(tmp_path: Path):
    project_root = tmp_path / "consumer"
    project_root.mkdir()
    manifest_path = project_root / "alfrd.yaml"
    manifest_path.write_text(
        """\
name: integrated
entrypoint:
  - name: build
    cmd: [python, -m, consumer]
artifacts:
  - name: report
    path_pattern: products/{dataset_id}.txt
    description: Build report
    media_type: text/plain
""",
        encoding="utf-8",
    )

    manifest = load_manifest(project_root)
    assert manifest.artifacts == (
        ArtifactDefinition(
            name="report",
            path_pattern="products/{dataset_id}.txt",
            description="Build report",
            media_type="text/plain",
        ),
    )

    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    service = RuntimeService(store)
    project, workflows = service.register_manifest(manifest_path)
    dataset = service.create_dataset(project.id, "target-a", metadata={"band": "X"})

    frame = LogFrame(pd.DataFrame(), primary_colname="dataset_id")
    output = tmp_path / "report.txt"
    result = RuntimePipelineRunner(service).run(
        workflows[0].id,
        dataset.id,
        [BuildArtifact()],
        params={"output_path": str(output)},
        event_sinks=[LogFrameEventSink(frame)],
    )

    persisted = service.get_run(result.run_id)
    assert result.success
    assert persisted.status == "succeeded"
    assert persisted.step_executions[0].status == "succeeded"
    assert len(persisted.artifacts) == 1
    assert persisted.artifacts[0].media_type == "text/plain"
    assert persisted.artifacts[0].metadata_json["kind"] == "report"
    assert artifact_ref_from_model(persisted.artifacts[0]) == ArtifactRef(
        output,
        kind="report",
        description="Build report",
        metadata={"dataset": "target-a"},
        media_type="text/plain",
    )
    assert frame.get_value("build", primary_value="target-a") == "succeeded"

    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'catalog-shell.sqlite'}",
            "CATALOG_READER": RuntimeCatalogReader(service),
        }
    )
    client = app.test_client()
    assert client.get("/api/projects").get_json()["projects"][0]["name"] == "integrated"
    assert client.get("/api/projects/integrated/manifest").get_json()["validation"]["valid"]
    assert client.get("/api/projects/integrated/workflows/build").get_json()["sequence"] == [
        "build"
    ]
    artifact = client.get(
        "/api/projects/integrated/artifact-definitions/report"
    ).get_json()
    assert artifact["path_pattern"] == "products/{dataset_id}.txt"


def test_pipeline_failure_persists_failed_and_skipped_steps(tmp_path: Path):
    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    service = RuntimeService(store)
    project = service.create_project("failures", tmp_path / "project")
    workflow = service.create_workflow(
        project.id,
        "failure",
        [
            {"key": "fail", "command": ["false"]},
            {"key": "later", "command": ["true"]},
        ],
    )
    dataset = service.create_dataset(project.id, "target-b")
    frame = LogFrame(pd.DataFrame(), primary_colname="dataset_id")

    result = RuntimePipelineRunner(service).run(
        workflow.id,
        dataset.id,
        [Fail(), NeverRuns()],
        event_sinks=[LogFrameEventSink(frame)],
    )

    persisted = service.get_run(result.run_id)
    assert not result.success
    assert persisted.status == "failed"
    assert [item.status for item in persisted.step_executions] == ["failed", "skipped"]
    assert frame.get_value("fail", primary_value="target-b") == "failed"
    assert frame.get_value("later", primary_value="target-b") == "skipped"
