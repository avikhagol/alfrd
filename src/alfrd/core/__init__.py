"""Public compatibility classes for ALFRD's 0.2.1.0 baseline."""

from alfrd.core.project import Project, ProjectConfiguration
from alfrd.core.workflow import Workflow, WorkflowConfig, WorkflowManager
from alfrd.config import BaseConfig, CONFIG_MAPPING, Config
from alfrd.manifest import ProjectManifest, discover_manifest, load_manifest
from alfrd.repository import RepositoryRecord, RepositoryService

__all__ = [
    "BaseConfig",
    "CONFIG_MAPPING",
    "Config",
    "Project",
    "ProjectManifest",
    "ProjectConfiguration",
    "RepositoryRecord",
    "RepositoryService",
    "Workflow",
    "WorkflowConfig",
    "WorkflowManager",
    "discover_manifest",
    "load_manifest",
]
