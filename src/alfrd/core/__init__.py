"""Public compatibility classes for ALFRD's 0.2.1.0 baseline."""

# Exports load on first use (PEP 562), so importing one submodule (alfrd.core.inspect,
# alfrd.core.project, ...) doesn't load pandas through the others.
_LAZY = {
    "alfrd.core.artifacts": ("ArtifactError", "ArtifactPathError", "ArtifactTemplateError", "KNOWN_ARTIFACT_KINDS", "ResolvedArtifact", "resolve_declared_artifacts", "resolve_within_root",),
    "alfrd.core.viewers": ("ArtifactViewerError", "render_artifact", "render_directory", "render_file", "render_gallery", "render_html", "render_image", "render_json", "render_log", "render_table", "render_text", "render_yaml",),
    "alfrd.core.logframe": ("LogFrame", "LogFrameAdapter", "LogFrameEventSink",),
    "alfrd.core.project": ("Project", "ProjectConfiguration",),
    "alfrd.core.pipeline": ("ArtifactRef", "append_step_result_csv", "BatchResult", "ColName", "CrashSnapshotAdapter", "DatasetFinished", "DatasetResult", "DatasetStarted", "FunctionPipelineStep", "FunctionPipelineStepValidator", "PipelineContext", "PipelineCore", "PipelineError", "PipelineStepBase", "PipelineStepValidatorBase", "PipelineStepValidatorResult", "ResultCSVAdapter", "RunFinished", "RunStarted", "StepFailed", "StepResult", "StepSkipped", "StepStarted", "StepSucceeded", "write_crash_snapshot",),
    "alfrd.core.workflow": ("Workflow", "WorkflowConfig", "WorkflowManager",),
    "alfrd.config": ("BaseConfig", "CONFIG_MAPPING", "Config",),
    "alfrd.manifest": ("ArtifactDefinition", "ProjectManifest", "discover_manifest", "load_manifest",),
    "alfrd.repository": ("RepositoryRecord", "RepositoryService",),
}
_EXPORTS = {name: module for module, names in _LAZY.items() for name in names}


def __getattr__(name: str):
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    value = getattr(importlib.import_module(_EXPORTS[name]), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))


__all__ = [
    "ArtifactDefinition",
    "ArtifactError",
    "ArtifactPathError",
    "ArtifactTemplateError",
    "ArtifactViewerError",
    "BaseConfig",
    "CONFIG_MAPPING",
    "Config",
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
    "ArtifactRef",
    "append_step_result_csv",
    "BatchResult",
    "ColName",
    "CrashSnapshotAdapter",
    "DatasetFinished",
    "DatasetResult",
    "DatasetStarted",
    "FunctionPipelineStep",
    "FunctionPipelineStepValidator",
    "PipelineContext",
    "PipelineCore",
    "PipelineError",
    "PipelineStepBase",
    "PipelineStepValidatorBase",
    "PipelineStepValidatorResult",
    "LogFrame",
    "LogFrameAdapter",
    "LogFrameEventSink",
    "Project",
    "ProjectManifest",
    "ProjectConfiguration",
    "RepositoryRecord",
    "RepositoryService",
    "ResultCSVAdapter",
    "RunFinished",
    "RunStarted",
    "StepFailed",
    "StepResult",
    "StepSkipped",
    "StepStarted",
    "StepSucceeded",
    "write_crash_snapshot",
    "Workflow",
    "WorkflowConfig",
    "WorkflowManager",
    "discover_manifest",
    "load_manifest",
]
