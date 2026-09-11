from __future__ import annotations

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
    def get_manifest(self, project_id: int) -> dict[str, Any]: ...
    def list_workflows(self, project_id: int) -> list[dict[str, Any]]: ...
    def get_workflow(self, project_id: int, name: str) -> dict[str, Any] | None: ...
    def list_steps(self, project_id: int) -> list[dict[str, Any]]: ...
    def get_step(self, project_id: int, name: str) -> dict[str, Any] | None: ...
    def list_validators(self, project_id: int) -> list[dict[str, Any]]: ...
    def get_validator(self, project_id: int, name: str) -> dict[str, Any] | None: ...
    def list_parameters(self, project_id: int) -> list[dict[str, Any]]: ...
    def get_parameter(self, project_id: int, name: str) -> dict[str, Any] | None: ...
    def list_dataset_columns(self, project_id: int) -> list[dict[str, Any]]: ...
    def get_dataset_column(self, project_id: int, name: str) -> dict[str, Any] | None: ...
    def list_artifact_definitions(self, project_id: int) -> list[dict[str, Any]]: ...
    def get_artifact_definition(self, project_id: int, name: str) -> dict[str, Any] | None: ...


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
