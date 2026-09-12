"""Read-only, backend-only render/data-preparation helpers for artifact viewers.

These functions never trust a caller-supplied path directly: every one
resolves its target through :func:`alfrd.core.artifacts.resolve_within_root`
first, so a path/glob that escapes the dataset/run root is rejected before
any filesystem read happens. They return plain dicts intended to be handed to
a template (Flask or otherwise) or serialized to JSON for an API response;
none of them render HTML themselves, keeping the safety-critical resolution
logic independent of any particular presentation layer.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import yaml

from alfrd.core.artifacts import ArtifactError, resolve_within_root

DEFAULT_TEXT_LIMIT = 200_000
DEFAULT_TABLE_ROW_LIMIT = 500
DEFAULT_DIRECTORY_ENTRY_LIMIT = 2000


class ArtifactViewerError(ArtifactError):
    """A viewer could not safely prepare data for an artifact."""


def _resolved(root: str | Path, candidate: str | Path) -> Path:
    return resolve_within_root(root, candidate)


def _require_exists(path: Path) -> None:
    if not path.exists():
        raise ArtifactViewerError(f"artifact path does not exist: {path}")


def render_file(root: str | Path, candidate: str | Path) -> dict[str, Any]:
    """Generic metadata for any single-file (or non-directory) artifact."""

    path = _resolved(root, candidate)
    exists = path.exists()
    return {
        "kind": "file",
        "path": str(path),
        "name": path.name,
        "exists": exists,
        "size": path.stat().st_size if exists and path.is_file() else None,
        "is_dir": exists and path.is_dir(),
    }


def render_directory(
    root: str | Path,
    candidate: str | Path,
    *,
    limit: int = DEFAULT_DIRECTORY_ENTRY_LIMIT,
) -> dict[str, Any]:
    """List immediate children of a directory artifact, safely scoped.

    Every entry is a child of the already-resolved directory, so no further
    escape is possible even for maliciously named entries.
    """

    path = _resolved(root, candidate)
    _require_exists(path)
    if not path.is_dir():
        raise ArtifactViewerError(f"artifact is not a directory: {path}")
    entries = []
    for index, child in enumerate(sorted(path.iterdir(), key=lambda item: item.name)):
        if index >= limit:
            break
        entries.append(
            {
                "name": child.name,
                "is_dir": child.is_dir(),
                "size": child.stat().st_size if child.is_file() else None,
            }
        )
    total = sum(1 for _ in path.iterdir())
    return {
        "kind": "directory",
        "path": str(path),
        "entries": entries,
        "truncated": total > len(entries),
        "total_entries": total,
    }


def render_text(
    root: str | Path,
    candidate: str | Path,
    *,
    max_bytes: int = DEFAULT_TEXT_LIMIT,
    tail_lines: int | None = None,
) -> dict[str, Any]:
    """Read a text/log artifact, bounding memory use for large files.

    ``tail_lines`` (typically used for logs) returns only the last N lines
    read within the ``max_bytes`` window rather than the file head.
    """

    path = _resolved(root, candidate)
    _require_exists(path)
    if not path.is_file():
        raise ArtifactViewerError(f"artifact is not a file: {path}")
    size = path.stat().st_size
    with path.open("rb") as stream:
        if tail_lines is not None and size > max_bytes:
            stream.seek(max(0, size - max_bytes))
        raw = stream.read(max_bytes)
    text = raw.decode("utf-8", errors="replace")
    truncated = size > len(raw)
    if tail_lines is not None:
        lines = text.splitlines()[-tail_lines:]
        text = "\n".join(lines)
    return {
        "kind": "text",
        "path": str(path),
        "content": text,
        "truncated": truncated,
        "size": size,
    }


def render_log(
    root: str | Path,
    candidate: str | Path,
    *,
    max_bytes: int = DEFAULT_TEXT_LIMIT,
    tail_lines: int = 500,
) -> dict[str, Any]:
    """Log viewer: same safety and reading rules as text, tailed by default."""

    data = render_text(root, candidate, max_bytes=max_bytes, tail_lines=tail_lines)
    data["kind"] = "log"
    return data


def render_json(root: str | Path, candidate: str | Path) -> dict[str, Any]:
    path = _resolved(root, candidate)
    _require_exists(path)
    if not path.is_file():
        raise ArtifactViewerError(f"artifact is not a file: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactViewerError(f"could not parse JSON artifact {path}: {exc}") from exc
    return {"kind": "json", "path": str(path), "data": data}


def render_yaml(root: str | Path, candidate: str | Path) -> dict[str, Any]:
    path = _resolved(root, candidate)
    _require_exists(path)
    if not path.is_file():
        raise ArtifactViewerError(f"artifact is not a file: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ArtifactViewerError(f"could not parse YAML artifact {path}: {exc}") from exc
    return {"kind": "yaml", "path": str(path), "data": data}


def render_table(
    root: str | Path,
    candidate: str | Path,
    *,
    delimiter: str = ",",
    max_rows: int = DEFAULT_TABLE_ROW_LIMIT,
) -> dict[str, Any]:
    """Read a delimited table artifact (e.g. CSV) without a hard dependency.

    Uses the standard library ``csv`` module deliberately: table artifacts
    are a generic ALFRD viewer concern, not a place to require ``polars``.
    """

    path = _resolved(root, candidate)
    _require_exists(path)
    if not path.is_file():
        raise ArtifactViewerError(f"artifact is not a file: {path}")
    with path.open(newline="", encoding="utf-8", errors="replace") as stream:
        reader = csv.reader(stream, delimiter=delimiter)
        try:
            columns = next(reader)
        except StopIteration:
            columns = []
        rows: list[list[str]] = []
        truncated = False
        for index, row in enumerate(reader):
            if index >= max_rows:
                truncated = True
                break
            rows.append(row)
    return {
        "kind": "table",
        "path": str(path),
        "columns": columns,
        "rows": rows,
        "truncated": truncated,
    }


def render_image(root: str | Path, candidate: str | Path) -> dict[str, Any]:
    path = _resolved(root, candidate)
    _require_exists(path)
    if not path.is_file():
        raise ArtifactViewerError(f"artifact is not a file: {path}")
    return {
        "kind": "image",
        "path": str(path),
        "name": path.name,
        "size": path.stat().st_size,
    }


def render_gallery(
    root: str | Path, candidates: list[str | Path] | tuple[str | Path, ...]
) -> dict[str, Any]:
    """Render an image_collection artifact as a gallery of image entries.

    Every candidate is independently resolved and rejected if it escapes
    root; one bad entry does not prevent listing the rest, but is reported.
    """

    images: list[dict[str, Any]] = []
    errors: list[str] = []
    for candidate in candidates:
        try:
            images.append(render_image(root, candidate))
        except ArtifactError as exc:
            errors.append(str(exc))
    return {"kind": "image_collection", "images": images, "errors": errors}


def render_html(root: str | Path, candidate: str | Path, **kwargs: Any) -> dict[str, Any]:
    """HTML artifacts are read as text; ALFRD never executes/renders them."""

    data = render_text(root, candidate, **kwargs)
    data["kind"] = "html"
    return data


_RENDERERS = {
    "file": render_file,
    "directory": render_directory,
    "text": render_text,
    "log": render_log,
    "json": render_json,
    "yaml": render_yaml,
    "table": render_table,
    "image": render_image,
    "html": render_html,
}


def render_artifact(
    kind: str, root: str | Path, candidate: str | Path, **options: Any
) -> dict[str, Any]:
    """Dispatch to the generic viewer for *kind*.

    ``image_collection`` and ``archive`` are intentionally excluded: the
    former needs a list of candidates (:func:`render_gallery`), and ALFRD has
    no safe generic archive-content viewer (an archive is presented like a
    ``file``).
    """

    if kind == "image_collection":
        raise ArtifactViewerError(
            "image_collection artifacts must use render_gallery with a candidate list"
        )
    if kind == "archive":
        return render_file(root, candidate)
    renderer = _RENDERERS.get(kind)
    if renderer is None:
        raise ArtifactViewerError(f"no generic viewer for artifact kind {kind!r}")
    return renderer(root, candidate, **options)


__all__ = [
    "ArtifactViewerError",
    "render_artifact",
    "render_directory",
    "render_file",
    "render_gallery",
    "render_html",
    "render_image",
    "render_json",
    "render_log",
    "render_table",
    "render_text",
    "render_yaml",
]
