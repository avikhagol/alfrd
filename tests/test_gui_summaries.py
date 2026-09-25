from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from alfrd.gui.summaries import build_summaries
from alfrd.manifest import load_manifest
from alfrd.runtime import RuntimeService, RuntimeStore


def test_builds_declarative_config_and_runtime_result_tables(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "alfrd.yaml").write_text(
        """name: demo
entrypoint:
  - {name: reduce, cmd: [python, reduce.py]}
summaries:
  config:
    title: Inputs
    rows:
      - {step: reduce, parameter: threads, value: 4}
  result:
    columns: [dataset, step, status, attempt, artifact_count]
""",
        encoding="utf-8",
    )
    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    service = RuntimeService(store)
    manifest = load_manifest(root)
    project, workflows = service.register_manifest(manifest)
    dataset = service.create_dataset(project.id, "target")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    service.import_historical_run(
        workflows[0].id,
        dataset.id,
        working_directory=run_dir,
        status="succeeded",
        executions=[
            {
                "key": "reduce",
                "status": "succeeded",
                "attempt": 2,
                "started_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
                "finished_at": datetime(2026, 1, 1, 0, 0, 2, tzinfo=timezone.utc),
            }
        ],
    )

    summaries = build_summaries({"id": project.id, "root_path": str(root)}, service)

    assert summaries["config"]["rows"] == [
        {"step": "reduce", "parameter": "threads", "source": "manifest snapshot", "value": 4}
    ]
    assert summaries["result"]["rows"][0] == {
        "dataset": "target",
        "step": "reduce",
        "status": "succeeded",
        "attempt": 2,
        "artifact_count": 0,
    }


def test_absent_and_malformed_manifests_are_graceful(tmp_path: Path):
    missing = build_summaries({"id": "missing", "root_path": str(tmp_path)}, None)
    assert missing["config"]["rows"] == []
    assert missing["config"]["error"] is not None
    assert missing["result"]["rows"] == []

    (tmp_path / "alfrd.yaml").write_text("name: [", encoding="utf-8")
    malformed = build_summaries({"id": "bad", "root_path": str(tmp_path)}, None)
    assert malformed["config"]["error"]
