from __future__ import annotations

from pathlib import Path

import pytest

from alfrd.core.artifacts import ArtifactPathError
from alfrd.core.viewers import (
    ArtifactViewerError,
    render_artifact,
    render_directory,
    render_file,
    render_gallery,
    render_html,
    render_image,
    render_json,
    render_log,
    render_table,
    render_text,
    render_yaml,
)


def test_render_file_reports_metadata(tmp_path: Path):
    target = tmp_path / "artifact.bin"
    target.write_bytes(b"0123456789")

    data = render_file(tmp_path, "artifact.bin")

    assert data["kind"] == "file"
    assert data["exists"] is True
    assert data["size"] == 10
    assert data["is_dir"] is False


def test_render_file_rejects_path_traversal(tmp_path: Path):
    with pytest.raises(ArtifactPathError):
        render_file(tmp_path, "../escape.bin")


def test_render_directory_lists_children_safely(tmp_path: Path):
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "b.txt").write_text("b", encoding="utf-8")
    (tmp_path / "run" / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "run" / "child").mkdir()

    data = render_directory(tmp_path, "run")

    assert data["kind"] == "directory"
    assert [entry["name"] for entry in data["entries"]] == ["a.txt", "b.txt", "child"]
    assert data["truncated"] is False


def test_render_directory_truncates_and_reports_total(tmp_path: Path):
    (tmp_path / "many").mkdir()
    for index in range(5):
        (tmp_path / "many" / f"f{index}.txt").write_text("x", encoding="utf-8")

    data = render_directory(tmp_path, "many", limit=2)

    assert len(data["entries"]) == 2
    assert data["truncated"] is True
    assert data["total_entries"] == 5


def test_render_directory_rejects_traversal(tmp_path: Path):
    with pytest.raises(ArtifactPathError):
        render_directory(tmp_path, "../")


def test_render_directory_rejects_non_directory(tmp_path: Path):
    (tmp_path / "file.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ArtifactViewerError, match="not a directory"):
        render_directory(tmp_path, "file.txt")


def test_render_text_reads_full_small_file(tmp_path: Path):
    (tmp_path / "log.txt").write_text("line1\nline2\n", encoding="utf-8")

    data = render_text(tmp_path, "log.txt")

    assert data["content"] == "line1\nline2\n"
    assert data["truncated"] is False


def test_render_text_bounds_large_reads(tmp_path: Path):
    (tmp_path / "big.txt").write_text("x" * 100, encoding="utf-8")

    data = render_text(tmp_path, "big.txt", max_bytes=10)

    assert len(data["content"]) == 10
    assert data["truncated"] is True


def test_render_text_missing_file_raises(tmp_path: Path):
    with pytest.raises(ArtifactViewerError, match="does not exist"):
        render_text(tmp_path, "missing.txt")


def test_render_text_rejects_traversal(tmp_path: Path):
    with pytest.raises(ArtifactPathError):
        render_text(tmp_path, "../secret.txt")


def test_render_log_tails_last_lines(tmp_path: Path):
    lines = [f"line-{i}\n" for i in range(10)]
    (tmp_path / "run.log").write_text("".join(lines), encoding="utf-8")

    data = render_log(tmp_path, "run.log", tail_lines=3)

    assert data["kind"] == "log"
    assert data["content"] == "line-7\nline-8\nline-9"


def test_render_json_parses_valid_document(tmp_path: Path):
    (tmp_path / "data.json").write_text('{"a": 1}', encoding="utf-8")

    data = render_json(tmp_path, "data.json")

    assert data["data"] == {"a": 1}


def test_render_json_rejects_invalid_document(tmp_path: Path):
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ArtifactViewerError, match="could not parse JSON"):
        render_json(tmp_path, "bad.json")


def test_render_yaml_parses_valid_document(tmp_path: Path):
    (tmp_path / "data.yaml").write_text("a: 1\nb: two\n", encoding="utf-8")

    data = render_yaml(tmp_path, "data.yaml")

    assert data["data"] == {"a": 1, "b": "two"}


def test_render_table_parses_csv_with_header(tmp_path: Path):
    (tmp_path / "result.csv").write_text("a,b\n1,2\n3,4\n", encoding="utf-8")

    data = render_table(tmp_path, "result.csv")

    assert data["columns"] == ["a", "b"]
    assert data["rows"] == [["1", "2"], ["3", "4"]]
    assert data["truncated"] is False


def test_render_table_truncates_rows(tmp_path: Path):
    rows = "\n".join(f"{i},{i}" for i in range(10))
    (tmp_path / "result.csv").write_text(f"a,b\n{rows}\n", encoding="utf-8")

    data = render_table(tmp_path, "result.csv", max_rows=3)

    assert len(data["rows"]) == 3
    assert data["truncated"] is True


def test_render_image_reports_metadata(tmp_path: Path):
    (tmp_path / "plot.png").write_bytes(b"\x89PNG")

    data = render_image(tmp_path, "plot.png")

    assert data["kind"] == "image"
    assert data["size"] == 4


def test_render_gallery_collects_images_and_reports_errors(tmp_path: Path):
    (tmp_path / "diag").mkdir()
    (tmp_path / "diag" / "a.png").write_bytes(b"a")

    data = render_gallery(
        tmp_path, ["diag/a.png", "../escape.png", "diag/missing.png"]
    )

    assert data["kind"] == "image_collection"
    assert len(data["images"]) == 1
    assert data["images"][0]["name"] == "a.png"
    assert len(data["errors"]) == 2


def test_render_html_reads_as_text_only(tmp_path: Path):
    (tmp_path / "report.html").write_text("<html>hi</html>", encoding="utf-8")

    data = render_html(tmp_path, "report.html")

    assert data["kind"] == "html"
    assert data["content"] == "<html>hi</html>"


def test_render_artifact_dispatches_by_kind(tmp_path: Path):
    (tmp_path / "data.json").write_text("{}", encoding="utf-8")

    data = render_artifact("json", tmp_path, "data.json")

    assert data["kind"] == "json"


def test_render_artifact_archive_falls_back_to_file_metadata(tmp_path: Path):
    (tmp_path / "bundle.tar.gz").write_bytes(b"x")

    data = render_artifact("archive", tmp_path, "bundle.tar.gz")

    assert data["kind"] == "file"


def test_render_artifact_rejects_image_collection_without_gallery(tmp_path: Path):
    with pytest.raises(ArtifactViewerError, match="render_gallery"):
        render_artifact("image_collection", tmp_path, "diag")


def test_render_artifact_rejects_unknown_kind(tmp_path: Path):
    with pytest.raises(ArtifactViewerError, match="no generic viewer"):
        render_artifact("unknown", tmp_path, "x")
