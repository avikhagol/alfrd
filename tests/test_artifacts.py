from __future__ import annotations

from pathlib import Path

import pytest

from alfrd.core.artifacts import (
    ArtifactPathError,
    ArtifactTemplateError,
    KNOWN_ARTIFACT_KINDS,
    ResolvedArtifact,
    is_glob_pattern,
    resolve_declared_artifacts,
    resolve_within_root,
)
from alfrd.core.pipeline import ArtifactRef
from alfrd.manifest import ArtifactDefinition


def test_known_artifact_kinds_match_roadmap_contract():
    assert KNOWN_ARTIFACT_KINDS == {
        "file",
        "directory",
        "table",
        "json",
        "yaml",
        "text",
        "log",
        "image",
        "image_collection",
        "html",
        "archive",
    }


def test_resolve_within_root_accepts_relative_descendant(tmp_path: Path):
    (tmp_path / "sub").mkdir()
    result = resolve_within_root(tmp_path, "sub/file.txt")
    assert result == (tmp_path / "sub" / "file.txt").resolve()


def test_resolve_within_root_accepts_the_root_itself(tmp_path: Path):
    assert resolve_within_root(tmp_path, ".") == tmp_path.resolve()


def test_resolve_within_root_rejects_parent_traversal(tmp_path: Path):
    with pytest.raises(ArtifactPathError, match="escapes root"):
        resolve_within_root(tmp_path, "../escape.txt")


def test_resolve_within_root_rejects_nested_traversal(tmp_path: Path):
    with pytest.raises(ArtifactPathError, match="escapes root"):
        resolve_within_root(tmp_path, "sub/../../escape.txt")


def test_resolve_within_root_rejects_absolute_paths(tmp_path: Path):
    with pytest.raises(ArtifactPathError, match="absolute"):
        resolve_within_root(tmp_path, "/etc/passwd")


def test_resolve_within_root_rejects_symlink_escape(tmp_path: Path):
    outside = tmp_path.parent / "outside-artifact-target"
    outside.mkdir(exist_ok=True)
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    root = tmp_path / "root"
    root.mkdir()
    link = root / "escape"
    link.symlink_to(outside)
    with pytest.raises(ArtifactPathError, match="escapes root"):
        resolve_within_root(root, "escape/secret.txt")


def test_is_glob_pattern_detects_metacharacters():
    assert is_glob_pattern("*.log")
    assert is_glob_pattern("logs/?.txt")
    assert is_glob_pattern("logs/[ab].txt")
    assert not is_glob_pattern("logs/plain.txt")


def test_resolve_declared_artifacts_plain_pattern_reports_existence(tmp_path: Path):
    (tmp_path / "products").mkdir()
    output = tmp_path / "products" / "target-a.txt"
    output.write_text("payload", encoding="utf-8")
    definition = ArtifactDefinition(
        name="result_table",
        path_pattern="products/{dataset_id}.txt",
        description="Result table",
        media_type="text/plain",
        kind="table",
    )

    resolved = resolve_declared_artifacts(
        [definition], tmp_path, {"dataset_id": "target-a"}
    )

    assert len(resolved) == 1
    artifact = resolved[0]
    assert isinstance(artifact, ResolvedArtifact)
    assert isinstance(artifact.ref, ArtifactRef)
    assert artifact.ref.path == output
    assert artifact.ref.kind == "table"
    assert artifact.ref.media_type == "text/plain"
    assert artifact.exists is True
    assert artifact.size == len(b"payload")


def test_resolve_declared_artifacts_reports_missing_without_erroring(tmp_path: Path):
    definition = ArtifactDefinition(name="missing", path_pattern="nope/{dataset_id}.txt")

    resolved = resolve_declared_artifacts([definition], tmp_path, {"dataset_id": "x"})

    assert resolved[0].exists is False
    assert resolved[0].size is None


def test_resolve_declared_artifacts_glob_matches_multiple(tmp_path: Path):
    diagnostics = tmp_path / "diag"
    diagnostics.mkdir()
    (diagnostics / "a.png").write_bytes(b"a")
    (diagnostics / "b.png").write_bytes(b"bb")
    definition = ArtifactDefinition(
        name="diagnostic_plots",
        path_pattern="diag/*.png",
        kind="image_collection",
    )

    resolved = resolve_declared_artifacts([definition], tmp_path, {})

    assert [item.ref.path.name for item in resolved] == ["a.png", "b.png"]
    assert all(item.ref.kind == "image_collection" for item in resolved)


def test_resolve_declared_artifacts_glob_rejects_parent_segment(tmp_path: Path):
    definition = ArtifactDefinition(name="bad", path_pattern="../*.png")
    with pytest.raises(ArtifactPathError, match="'\\.\\.'"):
        resolve_declared_artifacts([definition], tmp_path, {})


def test_resolve_declared_artifacts_glob_rejects_absolute_pattern(tmp_path: Path):
    definition = ArtifactDefinition(name="bad", path_pattern="/etc/*.conf")
    with pytest.raises(ArtifactPathError, match="absolute"):
        resolve_declared_artifacts([definition], tmp_path, {})


def test_resolve_declared_artifacts_requires_placeholder_value(tmp_path: Path):
    definition = ArtifactDefinition(name="needs_value", path_pattern="{missing}.txt")
    with pytest.raises(ArtifactTemplateError, match="missing"):
        resolve_declared_artifacts([definition], tmp_path, {})


def test_resolve_declared_artifacts_supports_nested_dotted_placeholders(tmp_path: Path):
    (tmp_path / "run-a").mkdir()
    output = tmp_path / "run-a" / "result.csv"
    output.write_text("x", encoding="utf-8")
    definition = ArtifactDefinition(
        name="result", path_pattern="{dataset.WORKDIR}/result.csv"
    )

    resolved = resolve_declared_artifacts(
        [definition], tmp_path, {"dataset": {"WORKDIR": "run-a"}}
    )

    assert resolved[0].ref.path == output
    assert resolved[0].exists is True


def test_resolved_artifact_to_dict_includes_existence_and_size(tmp_path: Path):
    output = tmp_path / "file.txt"
    output.write_text("hi", encoding="utf-8")
    definition = ArtifactDefinition(name="f", path_pattern="file.txt")

    resolved = resolve_declared_artifacts([definition], tmp_path, {})[0]
    data = resolved.to_dict()

    assert data["exists"] is True
    assert data["size"] == 2
    assert data["kind"] == "file"
    assert data["path"] == str(output)
