from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

SCHEMA_VERSION = 1


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
                    "VALUES (1, CURRENT_TIMESTAMP)"
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
