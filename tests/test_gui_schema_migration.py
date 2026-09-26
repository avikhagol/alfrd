"""Regression tests for GUI database schema initialization.

These guard against the "no such column" schema-drift bug: a fresh
database must produce a working /dashboard/, and an existing database
created by an incompatible/older schema must not be blindly reused by
``db.create_all()`` (which is a no-op against an existing table of the
same name, even if its columns disagree with the current ORM models).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from alfrd.gui import create_app


CATALOG_AND_MATRIX_ROUTES = [
    "/api/health",
    "/api/version",
    "/api/projects",
    "/dashboard/",
]


def _make_app(db_path: Path):
    return create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}",
        }
    )


def test_fresh_database_boots_and_dashboard_routes_return_200(tmp_path: Path):
    """A brand-new database file must produce a fully working app: every
    catalog/matrix/system route responds 200, never a 500 from a missing
    column."""
    db_path = tmp_path / "fresh.db"
    assert not db_path.exists()

    app = _make_app(db_path)
    client = app.test_client()

    for path in CATALOG_AND_MATRIX_ROUTES:
        response = client.get(path)
        assert response.status_code == 200, (path, response.status_code, response.get_data())


def test_stale_incompatible_database_is_migrated_not_reused(tmp_path: Path):
    """Simulate an existing database created by an older, incompatible
    schema (e.g. a legacy ``projects`` table without ``root_path``). Booting
    the app against it must not silently keep serving the broken table
    (``db.create_all()`` alone no-ops on an existing table name); it must
    detect the drift, move the legacy table aside, and (re)create the
    current schema so /dashboard/ still returns 200 instead of a 500
    OperationalError for a missing column."""
    db_path = tmp_path / "legacy.db"

    connection = sqlite3.connect(db_path)
    try:
        connection.executescript(
            """
            CREATE TABLE projects (
                id INTEGER NOT NULL,
                name VARCHAR(255) NOT NULL,
                description VARCHAR(255),
                configfile VARCHAR(255),
                param JSON,
                usesymlink BOOLEAN NOT NULL,
                registered_functions JSON,
                validator_functions JSON,
                validate_after JSON,
                validate_before JSON,
                PRIMARY KEY (id),
                UNIQUE (name)
            );
            CREATE TABLE workflows (
                id INTEGER NOT NULL,
                PRIMARY KEY (id)
            );
            """
        )
        connection.commit()
    finally:
        connection.close()

    app = _make_app(db_path)
    client = app.test_client()

    for path in CATALOG_AND_MATRIX_ROUTES:
        response = client.get(path)
        assert response.status_code == 200, (path, response.status_code, response.get_data())

    # The legacy table must have been preserved (renamed aside), not dropped.
    verify = sqlite3.connect(db_path)
    try:
        tables = {
            row[0]
            for row in verify.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        verify.close()
    assert "projects_legacy_backup" in tables
    assert "projects" in tables

    # And the recreated projects table now has the current schema's columns.
    verify = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in verify.execute("PRAGMA table_info(projects)")}
    finally:
        verify.close()
    assert "root_path" in columns


def test_dashboard_project_details_route_returns_200_with_seed_data(tmp_path: Path):
    """/dashboard/project/<name> is the other catalog+matrix-adjacent
    template route; assert it also renders 200 against a fresh database
    once a project row exists."""
    from alfrd.gui.model import db
    from alfrd.gui.model.tables import ProjectDB

    db_path = tmp_path / "fresh_with_project.db"
    app = _make_app(db_path)
    with app.app_context():
        db.session.add(ProjectDB(name="demo", description="demo project"))
        db.session.commit()

    client = app.test_client()
    response = client.get("/dashboard/project/demo")
    assert response.status_code == 200
