from __future__ import annotations

from pathlib import Path

import pytest

from alfrd.runtime import RuntimeService, RuntimeStore
from alfrd.runtime.matrix import MatrixQueryService, cell_detail


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "avica_run"
REAL_REDUCTIONS = Path(
    "/mnt/6438D98627D1388F/Intelligence/tests/vasco_0.3/reductions"
)
STEPS = [
    "preprocess_fitsidi",
    "fits_to_ms",
    "phaseshift",
    "avica_avg",
    "avicameta_ms",
    "avica_snr",
    "avica_fill_input",
    "avica_split_ms",
    "rpicard",
]


def _service(tmp_path: Path) -> RuntimeService:
    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    return RuntimeService(store)


def test_import_avica_run_latest_wins_status_timing_errors_artifacts_and_parameters(tmp_path):
    from alfrd.runtime.avica import import_avica_run

    service = _service(tmp_path)
    result = import_avica_run(
        service,
        FIXTURE_ROOT / "reductions",
        project_name="avica-test",
        steps=STEPS,
        project_root=FIXTURE_ROOT,
    )

    assert result.dataset_count == 1
    assert result.project.name == "avica-test"
    assert result.workflow.name == "avica"
    assert result.artifact_count == 6  # result CSV + five paths from detail JSON
    assert result.skipped_artifact_count == 0

    workflow = service.get_workflow(result.workflow.id)
    assert [step.key for step in workflow.steps] == STEPS
    assert workflow.parameters_json == {
        "folder_for_fits": "/data/fits/",
        "mpi_cores_rpicard": 3,
        "size_limit": 2000.0,
        "target_dir": "reductions/",
    }

    matrix = MatrixQueryService(service).build(result.project.id, result.workflow.id)
    row = matrix.rows[0]
    assert row.dataset_external_id == "TARGET_A"
    assert row.run_status == "failed"
    assert row.cells["avica_avg"].status == "succeeded"  # supersedes old failure
    assert row.cells["avica_avg"].attempt == 2
    assert row.cells["rpicard"].status == "failed"
    assert row.cells["phaseshift"].status == "skipped"
    assert row.cells["rpicard"].duration_seconds == 2.0
    assert row.cells["rpicard"].artifact_count == 2

    detail = cell_detail(service, row.cells["rpicard"].execution_id)
    assert "calibration failed" in detail["error"]
    assert {item["name"] for item in detail["artifacts"]} == {
        "X: X_calibrated.uvf",
        "S: S_calibrated.uvf",
    }


def test_import_avica_run_never_writes_source_tree(tmp_path):
    from alfrd.runtime.avica import import_avica_run

    source_files_before = sorted(
        (path.relative_to(FIXTURE_ROOT), path.stat().st_mtime_ns)
        for path in FIXTURE_ROOT.rglob("*")
        if path.is_file()
    )
    service = _service(tmp_path)
    import_avica_run(
        service,
        FIXTURE_ROOT / "reductions",
        project_name="read-only",
        steps=STEPS,
        project_root=FIXTURE_ROOT,
    )
    source_files_after = sorted(
        (path.relative_to(FIXTURE_ROOT), path.stat().st_mtime_ns)
        for path in FIXTURE_ROOT.rglob("*")
        if path.is_file()
    )
    assert source_files_after == source_files_before
    assert not list(FIXTURE_ROOT.rglob(".alfrd"))


def test_imported_avica_parameters_and_dataset_columns_render_in_dashboard(tmp_path):
    from alfrd.gui import create_app
    from alfrd.gui.services import RuntimeCatalogReader
    from alfrd.runtime.avica import import_avica_run

    service = _service(tmp_path)
    manifest_root = Path(__file__).parents[1] / "examples" / "avica_0.3"
    import_avica_run(
        service,
        FIXTURE_ROOT / "reductions",
        project_name="avica-dashboard",
        steps=STEPS,
        project_root=manifest_root,
    )
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'catalog.sqlite'}",
            "RUNTIME_SERVICE": service,
            "CATALOG_READER": RuntimeCatalogReader(service),
        }
    )
    response = app.test_client().get("/dashboard/project/avica-dashboard")
    assert response.status_code == 200
    assert b"mpi_cores_rpicard" in response.data
    assert b"TARGET_NAME" in response.data
    assert b"Dataset \xc3\x97 step matrix" in response.data


@pytest.mark.skipif(not REAL_REDUCTIONS.is_dir(), reason="real AVICA data is not mounted")
def test_real_avica_reductions_parse_read_only(tmp_path):
    from alfrd.runtime.avica import import_avica_run

    service = _service(tmp_path)
    result = import_avica_run(
        service,
        REAL_REDUCTIONS,
        project_name="avica-real-smoke",
        steps=STEPS,
        project_root=REAL_REDUCTIONS.parent,
    )
    assert result.dataset_count >= 3
    assert {item.external_id for item in service.list_datasets(result.project.id)} >= {
        "0742+103",
        "1309+555",
        "3C274",
    }
