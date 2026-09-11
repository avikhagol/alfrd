"""Typed, import-free loading of ``alfrd.yaml`` project manifests."""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import yaml
from jsonschema import Draft202012Validator

MANIFEST_FILENAME = "alfrd.yaml"
MANIFEST_VERSION = 1
_SCHEMA_FILENAME = "project-manifest-v1.schema.json"


class ManifestError(ValueError):
    """An ALFRD manifest cannot be read or validated."""


class ManifestNotFoundError(FileNotFoundError, ManifestError):
    """No ``alfrd.yaml`` was found at or above the requested location."""


def _extras(data: Mapping[str, Any], known: set[str]) -> Mapping[str, Any]:
    return MappingProxyType({key: value for key, value in data.items() if key not in known})


@dataclass(frozen=True)
class Entrypoint:
    """A named command exposed by a consumer project."""

    name: str
    cmd: tuple[str, ...]
    extra: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "cmd": list(self.cmd), **dict(self.extra)}


@dataclass(frozen=True)
class SchemaDefinition:
    """Named group of dotted consumer definitions.

    Definitions remain strings deliberately: loading a manifest must never import
    or execute code from the consumer repository.
    """

    name: str
    definitions: tuple[str, ...]
    extra: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "definitions": list(self.definitions),
            **dict(self.extra),
        }


# A short compatibility alias matching the manifest's ``schema`` key.
ProjectSchema = SchemaDefinition


@dataclass(frozen=True)
class ArtifactDefinition:
    """A declaration for an artifact a workflow may produce.

    This differs from ``ArtifactRef`` (one produced value) and the runtime
    ``Artifact`` row (one persisted value).
    """

    name: str
    path_pattern: str
    description: str = ""
    media_type: str | None = None
    extra: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": self.name,
            "path_pattern": self.path_pattern,
        }
        if self.description:
            data["description"] = self.description
        if self.media_type is not None:
            data["media_type"] = self.media_type
        data.update(self.extra)
        return data


@dataclass(frozen=True)
class ProjectManifest:
    """Validated ALFRD project manifest."""

    name: str
    entrypoint: tuple[Entrypoint, ...] = ()
    schema: tuple[SchemaDefinition, ...] = ()
    version: int = MANIFEST_VERSION
    path: Path | None = field(default=None, compare=False)
    extra: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)
    artifacts: tuple[ArtifactDefinition, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = {
            "version": self.version,
            "name": self.name,
            "entrypoint": [entry.to_dict() for entry in self.entrypoint],
            "schema": [definition.to_dict() for definition in self.schema],
        }
        if self.artifacts:
            data["artifacts"] = [artifact.to_dict() for artifact in self.artifacts]
        data.update(self.extra)
        return data

    def get_entrypoint(self, name: str) -> Entrypoint:
        for entry in self.entrypoint:
            if entry.name == name:
                return entry
        raise KeyError(f"No entrypoint named {name!r}")


def discover_manifest(start: str | Path | None = None) -> Path:
    """Find ``alfrd.yaml`` by walking from *start* toward the filesystem root."""

    candidate = Path.cwd() if start is None else Path(start).expanduser()
    if candidate.is_file():
        if candidate.name == MANIFEST_FILENAME:
            return candidate.resolve()
        candidate = candidate.parent
    else:
        candidate = candidate.resolve()

    for directory in (candidate, *candidate.parents):
        manifest = directory / MANIFEST_FILENAME
        if manifest.is_file():
            return manifest.resolve()

    raise ManifestNotFoundError(
        f"Could not find {MANIFEST_FILENAME!r} from {candidate} or any parent directory"
    )


def get_manifest_schema(version: int = MANIFEST_VERSION) -> dict[str, Any]:
    """Return a packaged JSON Schema without consulting consumer code."""

    if version != MANIFEST_VERSION:
        raise ManifestError(f"Unsupported manifest version: {version!r}")
    schema_file = resources.files("alfrd.schemas").joinpath(_SCHEMA_FILENAME)
    import json

    return json.loads(schema_file.read_text(encoding="utf-8"))


def validate_manifest(data: Mapping[str, Any]) -> None:
    """Validate raw manifest data against its versioned packaged schema."""

    version = data.get("version", MANIFEST_VERSION)
    if version != MANIFEST_VERSION:
        raise ManifestError(f"Unsupported manifest version: {version!r}")

    errors = sorted(
        Draft202012Validator(get_manifest_schema(version)).iter_errors(dict(data)),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "manifest"
        raise ManifestError(f"Invalid manifest at {location}: {error.message}")


def _unique_names(
    items: tuple[Entrypoint | SchemaDefinition | ArtifactDefinition, ...], section: str
) -> None:
    seen: set[str] = set()
    for item in items:
        if item.name in seen:
            raise ManifestError(f"Duplicate {section} name: {item.name!r}")
        seen.add(item.name)


def parse_manifest(
    data: Mapping[str, Any], *, source: str | Path | None = None
) -> ProjectManifest:
    """Validate and convert a mapping into typed manifest models."""

    if not isinstance(data, Mapping):
        raise ManifestError("Manifest root must be a mapping/object")
    raw = dict(data)
    validate_manifest(raw)

    entries = tuple(
        Entrypoint(
            name=item["name"],
            cmd=tuple(item["cmd"]),
            extra=_extras(item, {"name", "cmd"}),
        )
        for item in raw.get("entrypoint", [])
    )
    schemas = tuple(
        SchemaDefinition(
            name=item["name"],
            definitions=tuple(item["definitions"]),
            extra=_extras(item, {"name", "definitions"}),
        )
        for item in raw.get("schema", [])
    )
    artifacts = tuple(
        ArtifactDefinition(
            name=item["name"],
            path_pattern=item["path_pattern"],
            description=item.get("description", ""),
            media_type=item.get("media_type"),
            extra=_extras(
                item, {"name", "path_pattern", "description", "media_type"}
            ),
        )
        for item in raw.get("artifacts", [])
    )
    _unique_names(entries, "entrypoint")
    _unique_names(schemas, "schema")
    _unique_names(artifacts, "artifact")

    return ProjectManifest(
        version=raw.get("version", MANIFEST_VERSION),
        name=raw["name"],
        entrypoint=entries,
        schema=schemas,
        artifacts=artifacts,
        path=Path(source).expanduser().resolve() if source is not None else None,
        extra=_extras(raw, {"version", "name", "entrypoint", "schema", "artifacts"}),
    )


def load_manifest(path: str | Path | None = None) -> ProjectManifest:
    """Discover (when needed), safely parse, and validate one project manifest."""

    manifest_path = discover_manifest(path)
    try:
        data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ManifestError(f"Could not read YAML manifest {manifest_path}: {exc}") from exc
    if not isinstance(data, Mapping):
        raise ManifestError(f"YAML manifest root must be a mapping/object: {manifest_path}")
    return parse_manifest(data, source=manifest_path)


__all__ = [
    "ArtifactDefinition",
    "Entrypoint",
    "MANIFEST_FILENAME",
    "MANIFEST_VERSION",
    "ManifestError",
    "ManifestNotFoundError",
    "ProjectManifest",
    "ProjectSchema",
    "SchemaDefinition",
    "discover_manifest",
    "get_manifest_schema",
    "load_manifest",
    "parse_manifest",
    "validate_manifest",
]
