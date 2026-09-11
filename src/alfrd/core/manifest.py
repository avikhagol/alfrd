"""Compatibility exports for project manifest APIs."""

from alfrd.manifest import (
    Entrypoint,
    MANIFEST_FILENAME,
    MANIFEST_VERSION,
    ManifestError,
    ManifestNotFoundError,
    ProjectManifest,
    ProjectSchema,
    SchemaDefinition,
    discover_manifest,
    get_manifest_schema,
    load_manifest,
    parse_manifest,
    validate_manifest,
)

__all__ = [
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
