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
    def _project_dict(project) -> dict[str, Any]:
        return {
            "id": project.id,
            "name": project.name,
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
        return [self._project_dict(item) for item in self.service.list_projects()]

    def get_project(self, name: str) -> dict[str, Any] | None:
        from alfrd.runtime import RuntimeNotFound

        try:
            return self._project_dict(self.service.get_project_by_name(name))
        except RuntimeNotFound:
            return None

    def get_manifest(self, project_id: str) -> dict[str, Any]:
        from alfrd.manifest import ManifestError, load_manifest

        project = self.service.get_project(project_id)
        try:
            manifest = load_manifest(project.root_path)
        except ManifestError as error:
            return {
                "path": str(Path(project.root_path) / "alfrd.yaml"),
                "validation": {"valid": False, "errors": [str(error)]},
                "sync": {"state": "invalid"},
            }
        return {
            "path": str(manifest.path),
            "validation": {"valid": True, "errors": []},
            "sync": {"state": "synced"},
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
            columns = load_manifest(project.root_path).extra.get("dataset_columns", [])
        except ManifestError:
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
            artifacts = load_manifest(project.root_path).artifacts
        except ManifestError:
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


__all__ = ["CatalogReader", "RuntimeCatalogReader", "SqlAlchemyCatalogReader"]
