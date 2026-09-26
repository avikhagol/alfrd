from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from .models import Base
from .identity import project_identifier

SCHEMA_VERSION = 3


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
