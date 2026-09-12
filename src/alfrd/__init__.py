from __future__ import annotations

import os
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from platformdirs import user_cache_dir, user_config_dir

try:
    __version__ = version("alfrd")
except PackageNotFoundError:  # pragma: no cover - source trees are normally editable installs
    __version__ = "0.2.1.0"

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

from alfrd.plugins import (  # noqa: E402
    REGISTERED_STEPS,
    VALIDATE_AFTER,
    VALIDATE_BEFORE,
    VALIDATORS,
    List,
    PipelineRun,
    register,
    validate,
    validator,
)
from alfrd.util import B, X, c  # noqa: E402

import warnings as _warnings  # noqa: E402

with _warnings.catch_warnings():
    # The module-level singleton is a documented compatibility surface;
    # only warn when *user* code constructs PipelineRun directly.
    _warnings.simplefilter("ignore", DeprecationWarning)
    Pipeline = PipelineRun()

from alfrd.core.pipeline import (  # noqa: E402
    ArtifactRef,
    append_step_result_csv,
    BatchResult,
    ColName,
    CrashSnapshotAdapter,
    DatasetFinished,
    DatasetStarted,
    PipelineContext,
    PipelineCore,
    PipelineStepBase,
    PipelineStepValidatorBase,
    PipelineStepValidatorResult,
    ResultCSVAdapter,
    RunFinished,
    RunStarted,
    StepFailed,
    StepResult,
    StepSkipped,
    StepStarted,
    StepSucceeded,
    write_crash_snapshot,
)
from alfrd.core.artifacts import (  # noqa: E402
    ArtifactError,
    ArtifactPathError,
    ArtifactTemplateError,
    KNOWN_ARTIFACT_KINDS,
    ResolvedArtifact,
    resolve_declared_artifacts,
    resolve_within_root,
)
from alfrd.core.viewers import (  # noqa: E402
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
from alfrd.core.logframe import LogFrame, LogFrameAdapter, LogFrameEventSink  # noqa: E402
from alfrd.core.project import Project  # noqa: E402
from alfrd.core.workflow import Workflow  # noqa: E402
from alfrd.config import BaseConfig, CONFIG_MAPPING, Config  # noqa: E402
from alfrd.manifest import (  # noqa: E402
    ArtifactDefinition,
    Entrypoint,
    ManifestError,
    ManifestNotFoundError,
    ProjectManifest,
    ProjectSchema,
    SchemaDefinition,
    discover_manifest,
    load_manifest,
    parse_manifest,
    validate_manifest,
)
from alfrd.repository import (  # noqa: E402
    Repository,
    RepositoryNotFoundError,
    RepositoryRecord,
    RepositoryService,
    add_repository,
    inspect_repository,
    sync_repository,
)

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
