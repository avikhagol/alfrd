"""Core pipeline classes (PipelineCore, LogFrame) and the config/manifest/repository names."""

# Exports load on first use (PEP 562), so importing one submodule doesn't load pandas through the others.
_LAZY = {
    "alfrd.core.logframe": ("LogFrame", "LogFrameAdapter", "LogFrameEventSink",),
    "alfrd.core.pipeline": ("ArtifactRef", "append_step_result_csv", "BatchResult", "ColName", "CrashSnapshotAdapter", "DatasetFinished", "DatasetResult", "DatasetStarted", "FunctionPipelineStep", "FunctionPipelineStepValidator", "PipelineContext", "PipelineCore", "PipelineError", "PipelineStepBase", "PipelineStepValidatorBase", "PipelineStepValidatorResult", "ResultCSVAdapter", "RunFinished", "RunStarted", "StepFailed", "StepResult", "StepSkipped", "StepStarted", "StepSucceeded", "write_crash_snapshot",),
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
    "DatasetResult",
    "DatasetStarted",
    "discover_manifest",
    "FunctionPipelineStep",
    "FunctionPipelineStepValidator",
    "load_manifest",
    "LogFrame",
    "LogFrameAdapter",
    "LogFrameEventSink",
    "PipelineContext",
    "PipelineCore",
    "PipelineError",
    "PipelineStepBase",
    "PipelineStepValidatorBase",
    "PipelineStepValidatorResult",
    "ProjectManifest",
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
]
