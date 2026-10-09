"""Phase 1 of the plugin plan: the panel registry, unknown-panel validation, raw image/PDF bytes."""

from __future__ import annotations

from pathlib import Path

import pytest

import alfrd.layout_generic as lg
from test_studio_defs import served, tree  # noqa: F401 - fixtures

TREE = Path(__file__).parent / "fixtures" / "avica_tree"


@pytest.fixture(autouse=True)
def _restore_panels():
    before = dict(lg.PANELS)
    yield
    lg.PANELS.clear()
    lg.PANELS.update(before)
    lg._sync_kinds()


def test_builtin_panels_come_from_the_registry():
    assert set(lg.PANELS) == set(lg.PANEL_TYPES) | set(lg.CLIENT_PANELS)
    assert lg.PANEL_TYPES == ("file_status", "files", "json_fields", "csv_table", "text", "image")


def test_register_panel_validates_and_refuses_builtins():
    with pytest.raises(ValueError, match="built in"):
        lg.register_panel("text", lambda *a: {})
    with pytest.raises(ValueError, match="invalid panel kind"):
        lg.register_panel("Bad-Kind", lambda *a: {})
    with pytest.raises(ValueError, match="evaluate"):
        lg.register_panel("nothing")
    lg.register_panel("chart", client=True)
    assert "chart" in lg.CLIENT_PANELS and "chart" not in lg.PANEL_TYPES
    lg.unregister_panel("chart")
    lg.unregister_panel("text")  # built-ins stay
    assert "chart" not in lg.PANELS and "text" in lg.PANELS


def test_unknown_panel_lists_known_kinds_and_registered_kinds_validate():
    block = {"hierarchy": [{"level": "workdir", "dir": "wd"}], "views": {"metadata": [{"panel": "chart", "x": "time"}]}}
    errors = lg.validate(block)
    assert errors == [f"views/metadata/0/panel: unknown panel 'chart' (known: {', '.join(sorted(lg.PANELS))})"]
    lg.register_panel("chart", lambda root, panel, values, spec: {"x": panel.get("x")})
    assert lg.validate(block) == []  # plugin panels may take their own keys
    bad = lg.validate({"views": {"metadata": [{"panel": "text", "source": "a", "x": 1}]}})
    assert any("x" in e for e in bad), bad  # built-ins keep their strict keys


def _with_panel(tmp_path: Path, kind: str) -> Path:
    import shutil

    root = tmp_path / "proj"
    shutil.copytree(TREE, root)
    yaml = root / "alfrd.yaml"
    text = yaml.read_text()
    assert "views:" not in text
    yaml.write_text(text + f"views:\n  metadata:\n    - {{panel: {kind}, title: Mine, scope: workdir}}\n")
    return root


def test_registered_panel_output_in_view(tmp_path):
    root = _with_panel(tmp_path, "chart")
    seen = {}

    def evaluate(base, panel, values, spec):
        seen["spec"] = bool(spec.get("hierarchy"))
        return {"points": [1, 2], "workdir": values.get("workdir")}

    lg.register_panel("chart", evaluate)
    view = lg.view(root, {"target": "0742+103"})
    panel = next(p for p in view["panels"] if p["panel"] == "chart")
    assert view["errors"] == [] and seen["spec"]
    assert panel["instances"] and all(i["points"] == [1, 2] for i in panel["instances"])


def test_failing_plugin_evaluator_is_isolated(tmp_path):
    root = _with_panel(tmp_path, "boom")
    lg.register_panel("boom", lambda *a: 1 / 0)
    view = lg.view(root, {"target": "0742+103"})
    panel = next(p for p in view["panels"] if p["panel"] == "boom")
    assert panel["instances"][0]["error"] == "boom: division by zero"


def test_raw_file_serves_images_and_pdf_only(served):
    client, _, root = served
    yaml = root / "alfrd.yaml"
    yaml.write_text(yaml.read_text().replace(
        "  - name: casa_root_logs\n",
        "  - name: plots\n    path_pattern: \"plots/*\"\n    kind: log\n  - name: casa_root_logs\n", 1))
    diag = root / "plots"
    diag.mkdir()
    (diag / "plot.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    (diag / "report.pdf").write_bytes(b"%PDF-1.4\n")
    (diag / "page.html").write_text("<script>alert(1)</script>")
    (diag / "icon.svg").write_text("<svg/>")
    url = "/api/studio/projects/avica-t-0.3/file"
    png = client.get(url, query_string={"path": "plots/plot.png", "raw": "1"})
    assert png.status_code == 200, png.data
    assert png.mimetype == "image/png" and png.data.startswith(b"\x89PNG")
    assert png.headers["X-Content-Type-Options"] == "nosniff"
    pdf = client.get(url, query_string={"path": "plots/report.pdf", "raw": "1"})
    assert pdf.status_code == 200 and pdf.mimetype == "application/pdf"
    for name in ("page.html", "icon.svg"):
        assert client.get(url, query_string={"path": f"plots/{name}", "raw": "1"}).status_code == 415
    assert client.get(url, query_string={"path": "../x.png", "raw": "1"}).status_code == 400
    (root / "secret.png").write_bytes(b"x")
    assert client.get(url, query_string={"path": "secret.png", "raw": "1"}).status_code == 403  # not declared


def test_raw_file_serves_view_panel_sources(served):
    """D6: images/PDFs a views `image`/`text` panel declares open with raw=1 (still typed, still confined)."""
    client, _, root = served
    yaml = root / "alfrd.yaml"
    assert "views:" not in yaml.read_text()
    yaml.write_text(yaml.read_text() + "views:\n  target:\n"
                    "    - {panel: image, source: \"diag/{target}/*.png\"}\n"
                    "    - {panel: text, source: \"notes/*\"}\n")
    (root / "diag" / "0742+103").mkdir(parents=True)
    (root / "diag" / "0742+103" / "amp.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    (root / "notes").mkdir()
    (root / "notes" / "report.pdf").write_bytes(b"%PDF-1.4\n")
    (root / "notes" / "page.svg").write_text("<svg/>")
    (root / "other.png").write_bytes(b"x")
    url = "/api/studio/projects/avica-t-0.3/file"
    png = client.get(url, query_string={"path": "diag/0742+103/amp.png", "raw": "1"})
    assert png.status_code == 200 and png.mimetype == "image/png"
    assert png.headers["X-Content-Type-Options"] == "nosniff"
    assert client.get(url, query_string={"path": "notes/report.pdf", "raw": "1"}).status_code == 200
    assert client.get(url, query_string={"path": "notes/page.svg", "raw": "1"}).status_code == 415
    assert client.get(url, query_string={"path": "other.png", "raw": "1"}).status_code == 403
    assert client.get(url, query_string={"path": "diag/a/b/amp.png", "raw": "1"}).status_code in (403, 404)
    assert client.get(url, query_string={"path": "notes/../other.png", "raw": "1"}).status_code == 400
    assert lg.declared_source(root, "diag/x/amp.png") and not lg.declared_source(root, "/etc/x.png")
    (root / "notes" / "readme.md").write_text("# Notes\n")
    text = client.get(url, query_string={"path": "notes/readme.md"})
    assert text.status_code == 200 and text.data == b"# Notes\n"
    assert client.get(url, query_string={"path": "other.png"}).status_code == 403
