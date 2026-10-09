from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from .models import Base
from .identity import project_identifier

SCHEMA_VERSION = 4


def merge_folder_duplicates(connection) -> list[tuple[str, list[str]]]:
    """Fold every set of projects sharing one ``root_path`` into a single row.

    Older runtimes registered a folder again when its manifest was renamed. The
    oldest row survives (its id keeps the history); it takes the most recently
    updated row's name and description, and the identifier that matches that
    name when one of the rows already has it. Workflows, datasets (merged by
    ``external_id``), runs and audit events move to the survivor. Returns
    ``[(survivor identifier, [merged identifiers])]``.
    """
    rows = connection.execute(text(
        "SELECT id, identifier, name, root_path, description, created_at, updated_at "
        "FROM runtime_projects ORDER BY root_path, created_at, id"
    )).all()
    columns = {table: {c[1] for c in connection.execute(text(f"PRAGMA table_info({table})")).all()}
               for table in ("workflow_definitions", "datasets", "runs", "artifacts", "audit_events")}
    groups: dict[str, list] = {}
    for row in rows:
        groups.setdefault(row.root_path, []).append(row)
    merged: list[tuple[str, list[str]]] = []
    for root_path, group in groups.items():
        if len(group) < 2:
            continue
        survivor, duplicates = group[0], group[1:]
        latest = max(group, key=lambda r: (r.updated_at or "", r.created_at or ""))
        canonical = project_identifier(root_path, latest.name)
        identifier = canonical if any(r.identifier == canonical for r in group) else survivor.identifier
        for duplicate in duplicates:
            if "version" in columns["workflow_definitions"]:
                for workflow in connection.execute(text(
                    "SELECT id, name, version FROM workflow_definitions WHERE project_id = :p"
                ), {"p": duplicate.id}).all():
                    taken = connection.execute(text(
                        "SELECT MAX(version) FROM workflow_definitions WHERE project_id = :p AND name = :n"
                    ), {"p": survivor.id, "n": workflow.name}).scalar()
                    connection.execute(text(  # (project, name, version) is unique: append after the survivor's
                        "UPDATE workflow_definitions SET project_id = :p, version = :v WHERE id = :id"
                    ), {"p": survivor.id, "v": workflow.version if taken is None else taken + 1, "id": workflow.id})
            elif columns["workflow_definitions"]:
                connection.execute(text("UPDATE workflow_definitions SET project_id = :s WHERE project_id = :d"),
                                   {"s": survivor.id, "d": duplicate.id})
            for dataset in connection.execute(text(
                "SELECT id, external_id FROM datasets WHERE project_id = :p"
            ), {"p": duplicate.id}).all() if columns["datasets"] else []:
                kept = connection.execute(text(
                    "SELECT id FROM datasets WHERE project_id = :p AND external_id = :e"
                ), {"p": survivor.id, "e": dataset.external_id}).scalar()
                if kept is None:
                    connection.execute(text("UPDATE datasets SET project_id = :p WHERE id = :id"),
                                       {"p": survivor.id, "id": dataset.id})
                    continue
                for table in ("runs", "artifacts"):
                    if "dataset_id" in columns[table]:
                        connection.execute(text(f"UPDATE {table} SET dataset_id = :k WHERE dataset_id = :d"),
                                           {"k": kept, "d": dataset.id})
                if columns["audit_events"]:
                    connection.execute(text("UPDATE audit_events SET aggregate_id = :k "
                                            "WHERE aggregate_type = 'dataset' AND aggregate_id = :d"),
                                       {"k": kept, "d": dataset.id})
                connection.execute(text("DELETE FROM datasets WHERE id = :id"), {"id": dataset.id})
            if columns["audit_events"]:
                connection.execute(text("UPDATE audit_events SET aggregate_id = :s "
                                        "WHERE aggregate_type = 'project' AND aggregate_id = :d"),
                                   {"s": survivor.id, "d": duplicate.id})
            connection.execute(text("DELETE FROM runtime_projects WHERE id = :id"), {"id": duplicate.id})
        connection.execute(text(
            "UPDATE runtime_projects SET identifier = :i, name = :n, description = :d WHERE id = :id"
        ), {"i": identifier, "n": latest.name, "d": latest.description, "id": survivor.id})
        merged.append((identifier, [r.identifier for r in group if r.identifier != identifier]))
    return merged


class SchemaVersionError(RuntimeError):
    """The database schema is newer than this ALFRD runtime understands."""


class RuntimeStore:
    """SQLite persistence boundary for runtime state.

    Callers use short transaction-scoped sessions; services return detached ORM
    records with eagerly loaded relationships rather than exposing a global session.
    """

    def __init__(self, database: str | Path = ":memory:") -> None:
        self.database = database
        if str(database) == ":memory:":
            url = "sqlite+pysqlite:///:memory:"
        else:
            path = Path(database).expanduser().resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            url = f"sqlite+pysqlite:///{path}"
        self.engine: Engine = create_engine(url)
        event.listen(self.engine, "connect", self._configure_sqlite)
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)

    @staticmethod
    def _configure_sqlite(connection, _record) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    def initialize(self) -> None:
        """Apply forward-only schema migrations through the current version."""
        with self.engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE IF NOT EXISTS alfrd_schema_versions "
                "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            ))
            version = connection.execute(
                text("SELECT COALESCE(MAX(version), 0) FROM alfrd_schema_versions")
            ).scalar_one()
            if version > SCHEMA_VERSION:
                raise SchemaVersionError(
                    f"database schema {version} is newer than supported schema {SCHEMA_VERSION}"
                )
        if version < 1:
            Base.metadata.create_all(self.engine)
            with self.engine.begin() as connection:
                connection.execute(text(
                    "INSERT INTO alfrd_schema_versions(version, applied_at) "
                    "VALUES (:version, CURRENT_TIMESTAMP)"
                ), {"version": SCHEMA_VERSION})
            version = SCHEMA_VERSION
        if version < 2:
            # SQLite cannot drop the v1 UNIQUE(name) constraint in place. Rebuild
            # only the parent table while foreign-key checks are temporarily off;
            # child tables continue to reference the final runtime_projects name.
            raw = self.engine.raw_connection()
            try:
                cursor = raw.cursor()
                cursor.execute("PRAGMA foreign_keys=OFF")
                rows = cursor.execute(
                    "SELECT id, name, root_path, description, created_at, updated_at "
                    "FROM runtime_projects"
                ).fetchall()
                migrated_rows = []
                used_identifiers: set[str] = set()
                for row in rows:
                    identifier = project_identifier(row[2], row[1])
                    if identifier in used_identifiers:
                        # v1 allowed the same directory to be reconnected under a
                        # renamed manifest. Preserve both records without making
                        # startup ambiguous; the first retains the canonical key.
                        identifier = f"{identifier}.legacy.{row[0]}"
                    used_identifiers.add(identifier)
                    migrated_rows.append(
                        (row[0], identifier, row[1], row[2], row[3], row[4], row[5])
                    )
                cursor.execute(
                    "CREATE TABLE runtime_projects_v2 ("
                    "id VARCHAR(36) NOT NULL PRIMARY KEY, "
                    "identifier TEXT NOT NULL UNIQUE, "
                    "name VARCHAR(255) NOT NULL, "
                    "root_path TEXT NOT NULL, description TEXT, "
                    "created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"
                )
                cursor.executemany(
                    "INSERT INTO runtime_projects_v2 "
                    "(id, identifier, name, root_path, description, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    migrated_rows,
                )
                cursor.execute("DROP TABLE runtime_projects")
                cursor.execute("ALTER TABLE runtime_projects_v2 RENAME TO runtime_projects")
                cursor.execute("CREATE INDEX ix_runtime_projects_name ON runtime_projects (name)")
                cursor.execute(
                    "INSERT INTO alfrd_schema_versions(version, applied_at) "
                    "VALUES (2, CURRENT_TIMESTAMP)"
                )
                raw.commit()
                version = 2
            except BaseException:
                raw.rollback()
                raise
            finally:
                raw.cursor().execute("PRAGMA foreign_keys=ON")
                raw.close()
        if version < 3:
            with self.engine.begin() as connection:
                rows = connection.execute(text(
                    "SELECT id, name, root_path FROM runtime_projects"
                )).all()
                for project_id, name, root_path in rows:
                    connection.execute(
                        text("UPDATE runtime_projects SET identifier = :identifier WHERE id = :id"),
                        {"identifier": project_identifier(root_path, name), "id": project_id},
                    )
                connection.execute(text(
                    "INSERT INTO alfrd_schema_versions(version, applied_at) "
                    "VALUES (3, CURRENT_TIMESTAMP)"
                ))
        if version < 4:
            with self.engine.begin() as connection:
                merge_folder_duplicates(connection)
                connection.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ux_runtime_projects_root_path "
                    "ON runtime_projects (root_path)"
                ))
                connection.execute(text(
                    "INSERT INTO alfrd_schema_versions(version, applied_at) "
                    "VALUES (4, CURRENT_TIMESTAMP)"
                ))

    @property
    def schema_version(self) -> int:
        with self.engine.connect() as connection:
            return int(connection.execute(
                text("SELECT COALESCE(MAX(version), 0) FROM alfrd_schema_versions")
            ).scalar_one())

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self._sessions()
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    def close(self) -> None:
        self.engine.dispose()
