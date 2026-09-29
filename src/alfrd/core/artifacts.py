"""Declarative artifact resolution scoped safely under a dataset/run root.

This module adds no new artifact representation. ``alfrd.core.pipeline.ArtifactRef``
remains the single canonical reference shared by manifest declarations, pipeline
step results, and runtime persistence (see ``alfrd.runtime.adapters.artifact_ref_from_model``
for the runtime-row adapter). ``ResolvedArtifact`` here is a thin, disposable
wrapper pairing one ``ArtifactRef`` with filesystem facts (``exists``, ``size``)
computed at resolution time; it is not a competing artifact class.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from alfrd.core.pipeline import ArtifactRef
from alfrd.manifest import ArtifactDefinition

#: Generic artifact kinds recognized by declarative discovery and viewers.
#: A Measurement Set or other domain-specific output remains a generic
#: ``directory`` unless a project supplies an optional inspector; ALFRD must
#: never infer domain semantics (e.g. CASA/FITS) from a path pattern.
KNOWN_ARTIFACT_KINDS: frozenset[str] = frozenset(
    {
        "file",
        "directory",
        "table",
        "json",
        "yaml",
        "text",
        "log",
        "image",
        "image_collection",
        "collection",
        "html",
        "archive",
    }
)

_TOKEN_RE = re.compile(r"\{([a-zA-Z0-9_.]+)\}")
_GLOB_CHARS = frozenset("*?[")


class ArtifactError(ValueError):
    """Base error for declarative artifact resolution."""


class ArtifactPathError(ArtifactError):
    """A resolved artifact path is not safely scoped under its root."""


class ArtifactTemplateError(ArtifactError):
    """A path/glob pattern references a value that was not supplied."""


@dataclass(frozen=True)
class ResolvedArtifact:
    """One ``ArtifactRef`` paired with resolution-time filesystem facts."""

    ref: ArtifactRef
    exists: bool
    size: int | None = None

    def to_dict(self) -> dict[str, Any]:
        data = self.ref.to_dict()
        data["exists"] = self.exists
        data["size"] = self.size
        return data


def _substitute(pattern: str, values: Mapping[str, Any]) -> str:
    """Fill ``{dotted.key}`` placeholders from nested mappings only.

    Deliberately avoids ``str.format``'s attribute/getattr traversal (which
    could reach into arbitrary objects); only plain mapping lookups are used.
    """

    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        current: Any = values
        for part in key.split("."):
            if isinstance(current, Mapping) and part in current:
                current = current[part]
            else:
                raise ArtifactTemplateError(f"missing value for placeholder {key!r}")
        return str(current)

    return _TOKEN_RE.sub(repl, pattern)


def is_glob_pattern(value: str) -> bool:
    return any(char in value for char in _GLOB_CHARS)


def resolve_within_root(root: str | Path, candidate: str | Path) -> Path:
    """Resolve *candidate* under *root*, rejecting any path traversal/escape.

    Rejects absolute candidates outright (they must be root-relative), and
    rejects any normalized result that is not the root itself or a descendant
    of it -- this covers ``..`` segments and symlink-based escapes alike.
    """

    root_path = Path(root).expanduser().resolve()
    candidate_path = Path(candidate)
    if candidate_path.is_absolute():
        raise ArtifactPathError(
            f"artifact path {str(candidate)!r} must be relative to the root, not absolute"
        )
    combined = root_path / candidate_path
    resolved = combined.resolve()
    if resolved != root_path and not resolved.is_relative_to(root_path):
        raise ArtifactPathError(
            f"artifact path {str(candidate)!r} escapes root {root_path}"
        )
    return resolved


def _build(path: Path, definition: ArtifactDefinition) -> ResolvedArtifact:
    exists = path.exists()
    size = path.stat().st_size if exists and path.is_file() else None
    ref = ArtifactRef(
        path,
        kind=definition.kind,
        description=definition.description,
        media_type=definition.media_type,
        metadata={"name": definition.name},
    )
    return ResolvedArtifact(ref=ref, exists=exists, size=size)


def _resolve_glob(
    root: Path, formatted: str, definition: ArtifactDefinition
) -> list[ResolvedArtifact]:
    pattern_path = Path(formatted)
    if pattern_path.is_absolute():
        raise ArtifactPathError(
            f"artifact glob {formatted!r} must be relative to the root, not absolute"
        )
    if ".." in pattern_path.parts:
        raise ArtifactPathError(f"artifact glob {formatted!r} may not contain '..'")
    results: list[ResolvedArtifact] = []
    for match in sorted(root.glob(formatted)):
        resolved_match = match.resolve()
        if resolved_match != root and not resolved_match.is_relative_to(root):
            raise ArtifactPathError(
                f"artifact glob match {match!r} escapes root {root} (symlink escape)"
            )
        results.append(_build(resolved_match, definition))
    return results


def resolve_declared_artifacts(
    definitions: Iterable[ArtifactDefinition],
    root: str | Path,
    values: Mapping[str, Any],
) -> list[ResolvedArtifact]:
    """Resolve manifest ``path_pattern`` declarations without importing project code.

    *values* supplies placeholder substitutions, e.g. ``{"dataset_id": "a"}``
    for ``{dataset_id}`` or ``{"dataset": {"WORKDIR": "..."}}`` for
    ``{dataset.WORKDIR}``. Patterns containing glob metacharacters
    (``*``, ``?``, ``[``) are matched with :meth:`Path.glob` against *root*
    and may resolve to zero or more artifacts; plain patterns resolve to
    exactly one (whether or not the artifact currently exists).
    """

    root_path = Path(root).expanduser().resolve()
    resolved: list[ResolvedArtifact] = []
    for definition in definitions:
        formatted = _substitute(definition.path_pattern, values)
        if is_glob_pattern(formatted):
            resolved.extend(_resolve_glob(root_path, formatted, definition))
        else:
            path = resolve_within_root(root_path, formatted)
            resolved.append(_build(path, definition))
    return resolved


__all__ = [
    "ArtifactError",
    "ArtifactPathError",
    "ArtifactTemplateError",
    "KNOWN_ARTIFACT_KINDS",
    "ResolvedArtifact",
    "is_glob_pattern",
    "resolve_declared_artifacts",
    "resolve_within_root",
]
