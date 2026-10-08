"""Durable ALFRD execution state and local worker boundaries."""

# Exports load on first use (PEP 562): the shim and the plan runner import
# ``alfrd.runtime.<module>`` directly and must not pay for SQLAlchemy at start-up.
_LAZY = {
    ".models": ("Artifact", "AuditEvent", "Dataset", "Project", "Run", "StepDefinition", "StepExecution", "WorkflowDefinition",),
    ".matrix": ("MATRIX_STATUSES", "Matrix", "MatrixCell", "MatrixQueryService", "MatrixReader", "MatrixRow", "MatrixSummary", "cell_detail", "export_matrix_csv", "export_matrix_details_csv",),
    ".protocols": ("RuntimeOperations", "StepWorker",),
    ".service": ("DuplicateRunError", "InvalidTransition", "ParameterValidationError", "RuntimeNotFound", "RuntimeService", "Status",),
    ".store": ("SCHEMA_VERSION", "RuntimeStore", "SchemaVersionError",),
    ".worker": ("LocalSubprocessWorker", "run_workflow",),
}
_EXPORTS = {name: module for module, names in _LAZY.items() for name in names}


def __getattr__(name: str):
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    value = getattr(importlib.import_module(_EXPORTS[name], __name__), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))


__all__ = [
    "Artifact",
    "AuditEvent",
    "cell_detail",
    "Dataset",
    "DuplicateRunError",
    "export_matrix_csv",
    "export_matrix_details_csv",
    "InvalidTransition",
    "LocalSubprocessWorker",
    "Matrix",
    "MATRIX_STATUSES",
    "MatrixCell",
    "MatrixQueryService",
    "MatrixReader",
    "MatrixRow",
    "MatrixSummary",
    "ParameterValidationError",
    "Project",
    "Run",
    "run_workflow",
    "RuntimeNotFound",
    "RuntimeOperations",
    "RuntimeService",
    "RuntimeStore",
    "SCHEMA_VERSION",
    "SchemaVersionError",
    "Status",
    "StepDefinition",
    "StepExecution",
    "StepWorker",
    "WorkflowDefinition",
]
