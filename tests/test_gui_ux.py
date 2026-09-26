from pathlib import Path


TEMPLATES = Path(__file__).parents[1] / "src" / "alfrd" / "gui" / "templates" / "dashboard"


def test_dashboard_is_offline_usable_and_has_accessible_navigation():
    layout = (TEMPLATES / "layout.htm").read_text()
    css = (TEMPLATES.parents[1] / "static" / "alfrd.css").read_text()
    assert "cdn.jsdelivr.net" not in layout
    assert 'href="#main-content"' in layout
    assert "aria-current" in layout
    assert "@media" in css
    assert ".table-scroll" in css


def test_connect_and_artifact_forms_follow_shared_contract():
    connect = (TEMPLATES / "connect.htm").read_text()
    artifacts = (TEMPLATES / "artifacts.htm").read_text()
    for template in (connect, artifacts):
        assert 'method="post"' in template
        assert 'name="csrf_token"' in template
        assert 'id="form-errors"' in template
    assert 'name="path"' in connect
    for field in ("run_id", "step_execution_id", "path", "name", "media_type"):
        assert f'name="{field}"' in artifacts
    assert "data-run-id" in artifacts


def test_matrix_dialog_has_keyboard_close_and_safe_route_construction():
    matrix = (TEMPLATES / "matrix.htm").read_text()
    assert 'role="dialog"' in matrix
    assert 'aria-modal="true"' in matrix
    assert 'event.key === "Escape"' in matrix
    assert "encodeURIComponent(cell.dataset.executionId)" in matrix
    assert 'class="detail-grid"' in matrix
    assert "renderDetail(detail)" in matrix
    assert "detail.command || []" in matrix
    assert 'id="dataset-result-modal"' in matrix
    assert 'id="dataset-history-panel"' in matrix
    assert "renderDatasetResult(result)" in matrix
    assert "setInterval(refresh, 5000)" in matrix
