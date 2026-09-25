from __future__ import annotations

from pathlib import Path

from alfrd import get_alfrd_dir


class DefaultConfig:
    """Defaults for the optional, local read-only catalog application."""

    SQLALCHEMY_TRACK_MODIFICATIONS = False
    JSON_SORT_KEYS = False
    CATALOG_CREATE_SCHEMA = True
    RUNTIME_MUTATIONS_ENABLED = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    @staticmethod
    def database_uri() -> str:
        database = get_alfrd_dir() / "alfrd.db"
        return f"sqlite:///{database}"

    @classmethod
    def apply(cls, app) -> None:
        database_uri = cls.database_uri()
        if database_uri.startswith("sqlite:///"):
            Path(database_uri.removeprefix("sqlite:///")).parent.mkdir(
                parents=True, exist_ok=True
            )
        app.config.from_object(cls)
        app.config["SQLALCHEMY_DATABASE_URI"] = database_uri
