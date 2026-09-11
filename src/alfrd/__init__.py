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

Pipeline = PipelineRun()

from alfrd.core.logframe import LogFrame, LogFrameAdapter  # noqa: E402
from alfrd.core.project import Project  # noqa: E402
from alfrd.core.workflow import Workflow  # noqa: E402

__all__ = [
    "ALFRD_CACHE_DIR",
    "ALFRD_CONFIG_DIR",
    "ALFRD_DIR",
    "B",
    "List",
    "LogFrame",
    "LogFrameAdapter",
    "Pipeline",
    "PipelineRun",
    "PROJ_DIR",
    "Project",
    "REGISTERED_STEPS",
    "VALIDATE_AFTER",
    "VALIDATE_BEFORE",
    "VALIDATORS",
    "Workflow",
    "X",
    "__version__",
    "c",
    "get_alfrd_dir",
    "get_project_dir",
    "register",
    "validate",
    "validator",
]
