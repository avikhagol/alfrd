from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import select

from alfrd.gui.model import db
from alfrd.gui.model.tables import (
    ArtifactDefinitionDB,
    DatasetColumnDB,
    ParameterDB,
    ProjectDB,
    StepDB,
    ValidatorDB,
    WorkflowDB,
)
from alfrd.manifest import MANIFEST_ALIAS_FILENAME, MANIFEST_FILENAME, ManifestNotFoundError


def resolve_selected_manifest(path: str | Path) -> Path:
    """Resolve only the selected directory or supported named manifest file."""

    if not str(path).strip():
        raise ValueError("Project path is required.")
    selected = Path(path).expanduser()
    if selected.is_symlink() and not selected.is_dir():
        raise ValueError("Select a regular manifest file, not a symbolic link.")
    try:
        selected = selected.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ValueError(f"Project path does not exist: {selected}") from error
    if selected.is_dir():
        for filename in (MANIFEST_FILENAME, MANIFEST_ALIAS_FILENAME):
            candidate = selected / filename
            if candidate.is_symlink():
                raise ValueError("Manifest symbolic links are not accepted by the dashboard.")
            if candidate.is_file():
                return candidate.resolve()
        raise ManifestNotFoundError(
            f"No {MANIFEST_FILENAME!r} or {MANIFEST_ALIAS_FILENAME!r} exists in {selected}"
        )
    if not selected.is_file():
        raise ValueError("Project path must be a directory or regular manifest file.")
    if selected.name not in {MANIFEST_FILENAME, MANIFEST_ALIAS_FILENAME}:
        raise ValueError(
            f"Manifest filename must be {MANIFEST_FILENAME!r} or {MANIFEST_ALIAS_FILENAME!r}."
        )
    legacy = selected.parent / MANIFEST_FILENAME
    if selected.name == MANIFEST_ALIAS_FILENAME and legacy.is_file():
        if legacy.is_symlink():
            raise ValueError("Manifest symbolic links are not accepted by the dashboard.")
        return legacy.resolve()
    return selected


def resolve_artifact_path(working_directory: str | Path, relative_path: str) -> Path:
    """Validate a non-sensitive existing artifact contained by one run."""

    if not relative_path.strip():
        raise ValueError("Artifact path is required.")
    relative = Path(relative_path)
    if relative.is_absolute() or "\\" in relative_path or relative in {Path("."), Path("..")}:
        raise ValueError("Artifact path must be relative to the selected run directory.")
    for part in relative.parts:
        lowered = part.casefold()
        if (
            part in {"", ".", ".."}
            or part.startswith(".")
            or "secret" in lowered
            or "credential" in lowered
        ):
            raise ValueError("Dotfiles, secret paths, and traversal are not allowed.")
    try:
        root = Path(working_directory).expanduser().resolve(strict=True)
        candidate = (root / relative).resolve(strict=True)
        resolved_relative = candidate.relative_to(root)
    except (OSError, ValueError, RuntimeError) as error:
        raise ValueError("Artifact must exist inside the selected run directory.") from error
    if resolved_relative == Path(".") or any(
        part.startswith(".") or "secret" in part.casefold() or "credential" in part.casefold()
        for part in resolved_relative.parts
    ):
        raise ValueError("Dotfiles and secret paths cannot be registered through aliases.")
    if not (candidate.is_file() or candidate.is_dir()):
        raise ValueError("Artifact must be an existing regular file or directory.")
    return candidate


class CatalogReader(Protocol):
    """Narrow metadata-only interface consumed by the web layer."""

    def list_projects(self) -> list[dict[str, Any]]: ...
    def get_project(self, name: str) -> dict[str, Any] | None: ...
    def get_manifest(self, project_id: Any) -> dict[str, Any]: ...
    def list_workflows(self, project_id: Any) -> list[dict[str, Any]]: ...
    def get_workflow(self, project_id: Any, name: str) -> dict[str, Any] | None: ...
    def list_steps(self, project_id: Any) -> list[dict[str, Any]]: ...
    def get_step(self, project_id: Any, name: str) -> dict[str, Any] | None: ...
    def list_validators(self, project_id: Any) -> list[dict[str, Any]]: ...
    def get_validator(self, project_id: Any, name: str) -> dict[str, Any] | None: ...
    def list_parameters(self, project_id: Any) -> list[dict[str, Any]]: ...
    def get_parameter(self, project_id: Any, name: str) -> dict[str, Any] | None: ...
    def list_dataset_columns(self, project_id: Any) -> list[dict[str, Any]]: ...
    def get_dataset_column(self, project_id: Any, name: str) -> dict[str, Any] | None: ...
    def list_artifact_definitions(self, project_id: Any) -> list[dict[str, Any]]: ...
    def get_artifact_definition(self, project_id: Any, name: str) -> dict[str, Any] | None: ...


class SqlAlchemyCatalogReader:
    """Read normalized catalog records without loading project Python modules."""

    @staticmethod
    def _rows(model, project_id: int | None = None):
        statement = select(model)
        if project_id is not None:
            statement = statement.where(model.project_id == project_id)
        statement = statement.order_by(model.name)
        return db.session.scalars(statement).all()

    @staticmethod
    def _one(model, name: str, project_id: int | None = None):
        statement = select(model).where(model.name == name)
        if project_id is not None:
            statement = statement.where(model.project_id == project_id)
        return db.session.scalar(statement)

    def list_projects(self) -> list[dict[str, Any]]:
        return [row.to_dict() for row in self._rows(ProjectDB)]

    def get_project(self, name: str) -> dict[str, Any] | None:
        row = self._one(ProjectDB, name)
        return row.to_dict() if row else None

    def get_manifest(self, project_id: int) -> dict[str, Any]:
        row = db.session.get(ProjectDB, project_id)
        if row is None:
            raise LookupError(project_id)
        return row.manifest_dict()

    def list_workflows(self, project_id: int) -> list[dict[str, Any]]:
        return [row.to_dict() for row in self._rows(WorkflowDB, project_id)]

    def get_workflow(self, project_id: int, name: str) -> dict[str, Any] | None:
        row = self._one(WorkflowDB, name, project_id)
        return row.to_dict() if row else None

    def list_steps(self, project_id: int) -> list[dict[str, Any]]:
        rows = self._rows(StepDB, project_id)
        rows.sort(key=lambda row: (row.position, row.name))
        return [row.to_dict() for row in rows]

    def get_step(self, project_id: int, name: str) -> dict[str, Any] | None:
        row = self._one(StepDB, name, project_id)
        return row.to_dict() if row else None

    def list_validators(self, project_id: int) -> list[dict[str, Any]]:
        return [row.to_dict() for row in self._rows(ValidatorDB, project_id)]

    def get_validator(self, project_id: int, name: str) -> dict[str, Any] | None:
        row = self._one(ValidatorDB, name, project_id)
        return row.to_dict() if row else None

    def list_parameters(self, project_id: int) -> list[dict[str, Any]]:
        return [row.to_dict() for row in self._rows(ParameterDB, project_id)]

    def get_parameter(self, project_id: int, name: str) -> dict[str, Any] | None:
        row = self._one(ParameterDB, name, project_id)
        return row.to_dict() if row else None

    def list_dataset_columns(self, project_id: int) -> list[dict[str, Any]]:
        return [row.to_dict() for row in self._rows(DatasetColumnDB, project_id)]

    def get_dataset_column(self, project_id: int, name: str) -> dict[str, Any] | None:
        row = self._one(DatasetColumnDB, name, project_id)
        return row.to_dict() if row else None

    def list_artifact_definitions(self, project_id: int) -> list[dict[str, Any]]:
        return [row.to_dict() for row in self._rows(ArtifactDefinitionDB, project_id)]

    def get_artifact_definition(self, project_id: int, name: str) -> dict[str, Any] | None:
        row = self._one(ArtifactDefinitionDB, name, project_id)
        return row.to_dict() if row else None


class RuntimeCatalogReader:
    """Expose canonical runtime metadata through the read-only web contract.

    The Flask-SQLAlchemy tables remain a standalone catalog projection for
    existing deployments. This adapter avoids copying runtime rows into that
    competing schema when a ``RuntimeService`` is already available.
    """

    def __init__(self, service) -> None:
        self.service = service

    @staticmethod
    def _project_dict(project, duplicate: bool = False) -> dict[str, Any]:
        return {
            "id": project.id,
            "identifier": project.identifier,
            "name": project.name,
            "display_name": f"{project.name} ({Path(project.root_path).name})" if duplicate else project.name,
            "description": project.description,
            "root_path": project.root_path,
        }

    @staticmethod
    def _workflow_dict(workflow) -> dict[str, Any]:
        return {
            "id": workflow.id,
            "project_id": workflow.project_id,
            "name": workflow.name,
            "description": workflow.description,
            "version": workflow.version,
            "sequence": [step.key for step in workflow.steps],
        }

    def list_projects(self) -> list[dict[str, Any]]:
        projects = self.service.list_projects()
        counts = {item.name: sum(other.name == item.name for other in projects) for item in projects}
        return [self._project_dict(item, counts[item.name] > 1) for item in projects]

    def get_project(self, name: str) -> dict[str, Any] | None:
        from alfrd.runtime import RuntimeNotFound

        try:
            project = self.service.get_project_by_selector(name)
            duplicate = sum(item.name == project.name for item in self.service.list_projects()) > 1
            return self._project_dict(project, duplicate)
        except RuntimeNotFound:
            return None

    def get_manifest(self, project_id: str) -> dict[str, Any]:
        from alfrd.manifest import ManifestError, load_manifest

        project = self.service.get_project(project_id)
        try:
            manifest = load_manifest(resolve_selected_manifest(project.root_path))
        except (ManifestError, OSError, ValueError) as error:
            return {
                "path": str(Path(project.root_path) / "alfrd.yaml"),
                "validation": {"valid": False, "errors": [str(error)]},
                "sync": {"state": "invalid"},
            }
        expected = {
            entrypoint.name: (entrypoint.name, list(entrypoint.cmd))
            for entrypoint in manifest.entrypoint
        }
        persisted = {
            workflow.name: (workflow.steps[0].key, list(workflow.steps[0].command_json))
            for workflow in self.service.list_workflows(project_id)
            if len(workflow.steps) == 1
        }
        return {
            "path": str(manifest.path),
            "validation": {"valid": True, "errors": []},
            "sync": {"state": "synced" if expected == persisted else "out_of_sync"},
        }

    def list_workflows(self, project_id: str) -> list[dict[str, Any]]:
        return [
            self._workflow_dict(item)
            for item in self.service.list_workflows(project_id)
        ]

    def get_workflow(self, project_id: str, name: str) -> dict[str, Any] | None:
        matches = [
            item for item in self.service.list_workflows(project_id) if item.name == name
        ]
        return self._workflow_dict(matches[-1]) if matches else None

    def list_steps(self, project_id: str) -> list[dict[str, Any]]:
        rows = []
        for workflow in self.service.list_workflows(project_id):
            rows.extend(
                {
                    "id": step.id,
                    "project_id": project_id,
                    "workflow_id": workflow.id,
                    "name": step.key,
                    "description": None,
                    "position": step.position,
                    "command": list(step.command_json),
                }
                for step in workflow.steps
            )
        return rows

    def get_step(self, project_id: str, name: str) -> dict[str, Any] | None:
        return next((item for item in self.list_steps(project_id) if item["name"] == name), None)

    def list_validators(self, project_id: str) -> list[dict[str, Any]]:
        del project_id
        return []

    def get_validator(self, project_id: str, name: str) -> dict[str, Any] | None:
        del project_id, name
        return None

    def list_parameters(self, project_id: str) -> list[dict[str, Any]]:
        rows = []
        for workflow in self.service.list_workflows(project_id):
            rows.extend(
                {
                    "id": f"{workflow.id}:{name}",
                    "project_id": project_id,
                    "workflow_id": workflow.id,
                    "step_id": None,
                    "validator_id": None,
                    "name": name,
                    "description": None,
                    "type": type(value).__name__,
                    "required": False,
                    "default": value,
                }
                for name, value in sorted(workflow.parameters_json.items())
            )
        return rows

    def get_parameter(self, project_id: str, name: str) -> dict[str, Any] | None:
        return next(
            (item for item in self.list_parameters(project_id) if item["name"] == name),
            None,
        )

    def list_dataset_columns(self, project_id: str) -> list[dict[str, Any]]:
        from alfrd.manifest import ManifestError, load_manifest

        project = self.service.get_project(project_id)
        try:
            columns = load_manifest(resolve_selected_manifest(project.root_path)).extra.get("dataset_columns", [])
        except (ManifestError, OSError, ValueError):
            return []
        if not isinstance(columns, list):
            return []
        return [
            {
                "id": f"{project_id}:dataset-column:{item['name']}",
                "project_id": project_id,
                "name": item["name"],
                "type": item.get("type", "string"),
                "required": bool(item.get("required", False)),
                "description": item.get("description"),
            }
            for item in columns
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        ]

    def get_dataset_column(self, project_id: str, name: str) -> dict[str, Any] | None:
        return next(
            (item for item in self.list_dataset_columns(project_id) if item["name"] == name),
            None,
        )

    def list_artifact_definitions(self, project_id: str) -> list[dict[str, Any]]:
        from alfrd.manifest import ManifestError, load_manifest

        project = self.service.get_project(project_id)
        try:
            artifacts = load_manifest(resolve_selected_manifest(project.root_path)).artifacts
        except (ManifestError, OSError, ValueError):
            return []
        return [
            {
                "id": f"{project_id}:{item.name}",
                "project_id": project_id,
                "workflow_id": None,
                **item.to_dict(),
            }
            for item in artifacts
        ]

    def get_artifact_definition(self, project_id: str, name: str) -> dict[str, Any] | None:
        return next(
            (
                item
                for item in self.list_artifact_definitions(project_id)
                if item["name"] == name
            ),
            None,
        )


__all__ = [
    "CatalogReader",
    "RuntimeCatalogReader",
    "SqlAlchemyCatalogReader",
    "resolve_artifact_path",
    "resolve_selected_manifest",
]
