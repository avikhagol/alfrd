from __future__ import annotations

import os
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from platformdirs import user_cache_dir, user_config_dir

try:
    __version__ = version("alfrd")
except PackageNotFoundError:  # pragma: no cover - source trees are normally editable installs
    __version__ = "0.0.0+unknown"  # pyproject.toml holds the real version

ALFRD_CACHE_DIR = user_cache_dir("alfrd")
ALFRD_CONFIG_DIR = user_config_dir("alfrd")


def get_alfrd_dir() -> Path:
    """Return ALFRD's runtime data directory, honoring ``ALFRD_HOME``."""
    return Path(os.environ.get("ALFRD_HOME", "~/.alfrd")).expanduser()


def get_project_dir() -> Path:
    """Return the current project registry directory."""
    return get_alfrd_dir() / "projects"


# Compatibility constants. Internal code resolves the functions at use time so
# tests and applications can redirect ALFRD_HOME after importing the package.
ALFRD_DIR = get_alfrd_dir()
PROJ_DIR = get_project_dir()

# The public API loads on first use (PEP 562), so ``import alfrd`` stays cheap: runners,
# shims and the CLI start in a fraction of a second instead of importing pandas and SQLAlchemy.
_LAZY = {
    "alfrd.plugins": (
        "REGISTERED_STEPS",
        "VALIDATE_AFTER",
        "VALIDATE_BEFORE",
        "VALIDATORS",
        "List",
        "PipelineRun",
        "register",
        "validate",
        "validator",
    ),
    "alfrd.util": (
        "B",
        "X",
        "c",
    ),
    "alfrd.core.pipeline": (
        "ArtifactRef",
        "append_step_result_csv",
        "BatchResult",
        "ColName",
        "CrashSnapshotAdapter",
        "DatasetFinished",
        "DatasetStarted",
        "PipelineContext",
        "PipelineCore",
        "PipelineStepBase",
        "PipelineStepValidatorBase",
        "PipelineStepValidatorResult",
        "ResultCSVAdapter",
        "RunFinished",
        "RunStarted",
        "StepFailed",
        "StepResult",
        "StepSkipped",
        "StepStarted",
        "StepSucceeded",
        "write_crash_snapshot",
    ),
    "alfrd.core.artifacts": (
        "ArtifactError",
        "ArtifactPathError",
        "ArtifactTemplateError",
        "KNOWN_ARTIFACT_KINDS",
        "ResolvedArtifact",
        "resolve_declared_artifacts",
        "resolve_within_root",
    ),
    "alfrd.core.viewers": (
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
    ),
    "alfrd.core.logframe": (
        "LogFrame",
        "LogFrameAdapter",
        "LogFrameEventSink",
    ),
    "alfrd.core.project": (
        "Project",
    ),
    "alfrd.core.workflow": (
        "Workflow",
    ),
    "alfrd.config": (
        "BaseConfig",
        "CONFIG_MAPPING",
        "Config",
    ),
    "alfrd.manifest": (
        "ArtifactDefinition",
        "Entrypoint",
        "ManifestError",
        "ManifestNotFoundError",
        "ProjectManifest",
        "ProjectSchema",
        "SchemaDefinition",
        "discover_manifest",
        "load_manifest",
        "parse_manifest",
        "validate_manifest",
    ),
    "alfrd.repository": (
        "Repository",
        "RepositoryNotFoundError",
        "RepositoryRecord",
        "RepositoryService",
        "add_repository",
        "inspect_repository",
        "sync_repository",
    ),
}
_EXPORTS = {name: module for module, names in _LAZY.items() for name in names}


def __getattr__(name: str):
    import importlib

    if name == "Pipeline":
        import warnings

        from alfrd.plugins import PipelineRun

        with warnings.catch_warnings():
            # The module-level singleton is a documented compatibility surface;
            # only warn when *user* code constructs PipelineRun directly.
            warnings.simplefilter("ignore", DeprecationWarning)
            value = PipelineRun()
    elif name in _EXPORTS:
        value = getattr(importlib.import_module(_EXPORTS[name]), name)
    else:
        try:  # submodules, as eager imports used to set them (alfrd.core, alfrd.manifest, ...)
            return importlib.import_module(f"{__name__}.{name}")
        except ModuleNotFoundError as exc:
            if exc.name != f"{__name__}.{name}":
                raise
            raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))


__all__ = [
    "ALFRD_CACHE_DIR",
    "ALFRD_CONFIG_DIR",
    "ALFRD_DIR",
    "ArtifactDefinition",
    "ArtifactError",
    "ArtifactPathError",
    "ArtifactTemplateError",
    "ArtifactViewerError",
    "KNOWN_ARTIFACT_KINDS",
    "ResolvedArtifact",
    "resolve_declared_artifacts",
    "resolve_within_root",
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
    "B",
    "BaseConfig",
    "CONFIG_MAPPING",
    "Config",
    "Entrypoint",
    "ArtifactRef",
    "append_step_result_csv",
    "BatchResult",
    "ColName",
    "CrashSnapshotAdapter",
    "DatasetFinished",
    "DatasetStarted",
    "List",
    "LogFrame",
    "LogFrameAdapter",
    "LogFrameEventSink",
    "ManifestError",
    "ManifestNotFoundError",
    "Pipeline",
    "PipelineContext",
    "PipelineCore",
    "PipelineRun",
    "PipelineStepBase",
    "PipelineStepValidatorBase",
    "PipelineStepValidatorResult",
    "PROJ_DIR",
    "Project",
    "ProjectManifest",
    "ProjectSchema",
    "ResultCSVAdapter",
    "RunFinished",
    "RunStarted",
    "REGISTERED_STEPS",
    "Repository",
    "RepositoryNotFoundError",
    "RepositoryRecord",
    "RepositoryService",
    "SchemaDefinition",
    "VALIDATE_AFTER",
    "VALIDATE_BEFORE",
    "VALIDATORS",
    "StepFailed",
    "StepResult",
    "StepSkipped",
    "StepStarted",
    "StepSucceeded",
    "write_crash_snapshot",
    "Workflow",
    "X",
    "__version__",
    "c",
    "add_repository",
    "discover_manifest",
    "get_alfrd_dir",
    "get_project_dir",
    "inspect_repository",
    "load_manifest",
    "parse_manifest",
    "register",
    "sync_repository",
    "validate",
    "validate_manifest",
    "validator",
]
