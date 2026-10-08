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


# The public API loads on first use (PEP 562), so ``import alfrd`` stays cheap: runners,
# shims and the CLI start in a fraction of a second instead of importing pandas and SQLAlchemy.
_LAZY = {
    "alfrd.core.pipeline": ("ArtifactRef", "append_step_result_csv", "BatchResult", "ColName", "CrashSnapshotAdapter", "DatasetFinished", "DatasetStarted", "PipelineContext", "PipelineCore", "PipelineStepBase", "PipelineStepValidatorBase", "PipelineStepValidatorResult", "ResultCSVAdapter", "RunFinished", "RunStarted", "StepFailed", "StepResult", "StepSkipped", "StepStarted", "StepSucceeded", "write_crash_snapshot",),
    "alfrd.core.logframe": ("LogFrame", "LogFrameAdapter", "LogFrameEventSink",),
    "alfrd.config": ("BaseConfig", "CONFIG_MAPPING", "Config",),
    "alfrd.manifest": ("ArtifactDefinition", "Entrypoint", "ManifestError", "ManifestNotFoundError", "ProjectManifest", "SchemaDefinition", "discover_manifest", "load_manifest", "parse_manifest", "validate_manifest",),
    "alfrd.repository": ("Repository", "RepositoryNotFoundError", "RepositoryRecord", "RepositoryService", "add_repository", "inspect_repository", "sync_repository",),
}
_EXPORTS = {name: module for module, names in _LAZY.items() for name in names}


def __getattr__(name: str):
    import importlib

    if name in _EXPORTS:
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
    "__version__",
    "add_repository",
    "ALFRD_CACHE_DIR",
    "ALFRD_CONFIG_DIR",
    "append_step_result_csv",
    "ArtifactDefinition",
    "ArtifactRef",
    "BaseConfig",
    "BatchResult",
    "ColName",
    "Config",
    "CONFIG_MAPPING",
    "CrashSnapshotAdapter",
    "DatasetFinished",
    "DatasetStarted",
    "discover_manifest",
    "Entrypoint",
    "get_alfrd_dir",
    "inspect_repository",
    "load_manifest",
    "LogFrame",
    "LogFrameAdapter",
    "LogFrameEventSink",
    "ManifestError",
    "ManifestNotFoundError",
    "parse_manifest",
    "PipelineContext",
    "PipelineCore",
    "PipelineStepBase",
    "PipelineStepValidatorBase",
    "PipelineStepValidatorResult",
    "ProjectManifest",
    "Repository",
    "RepositoryNotFoundError",
    "RepositoryRecord",
    "RepositoryService",
    "ResultCSVAdapter",
    "RunFinished",
    "RunStarted",
    "SchemaDefinition",
    "StepFailed",
    "StepResult",
    "StepSkipped",
    "StepStarted",
    "StepSucceeded",
    "sync_repository",
    "validate_manifest",
    "write_crash_snapshot",
]
