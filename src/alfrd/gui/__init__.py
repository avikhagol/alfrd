def _ensure_catalog_schema(db) -> None:
    """Create or reconcile the catalog schema for the current ORM models.

    ``db.create_all()`` issues ``CREATE TABLE IF NOT EXISTS`` per model, so
    it silently no-ops when a table of the same name already exists with a
    different (older/legacy) column set -- e.g. a stale ``alfrd.db`` created
    by a previous, incompatible version of the catalog models. That leaves
    routes querying columns (such as ``projects.root_path``) that do not
    exist in the actual table, raising ``OperationalError`` at request time
    instead of at startup.

    To make both a brand-new database and an existing older database work,
    inspect each declared model's table before creating it: if the table is
    missing, create it normally; if it exists but is missing one or more
    columns the current model declares, the on-disk table is schema-drifted
    (belongs to an incompatible earlier version) and is not safe to query
    against the current ORM, so it is renamed aside (preserving the old data
    for inspection/manual migration) and recreated fresh.
    """
    from sqlalchemy import inspect

    engine = db.engine
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    for table in db.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        existing_columns = {col["name"] for col in inspector.get_columns(table.name)}
        expected_columns = {col.name for col in table.columns}
        missing = expected_columns - existing_columns
        if not missing:
            continue
        # Schema drift: the on-disk table predates the current model and is
        # missing columns the ORM expects to select. Move it aside rather
        # than losing data silently, then let create_all() build it fresh.
        backup_name = f"{table.name}_legacy_backup"
        with engine.begin() as connection:
            existing_backup_tables = set(inspect(engine).get_table_names())
            candidate = backup_name
            suffix = 0
            while candidate in existing_backup_tables:
                suffix += 1
                candidate = f"{backup_name}_{suffix}"
            connection.exec_driver_sql(
                f'ALTER TABLE "{table.name}" RENAME TO "{candidate}"'
            )

    db.create_all()


def create_app(config=None):
    """Create the optional read-only catalog application.

    Flask remains an optional dependency: importing :mod:`alfrd` itself does
    not import this module.  ``CATALOG_READER`` may be supplied by an
    integration; otherwise the bundled SQLAlchemy metadata adapter is used.
    """
    from flask import Flask, jsonify, request
    from werkzeug.exceptions import HTTPException

    app = Flask(__name__)

    import os

    if os.environ.get("ALFRD_TRUST_PROXY", "").strip().lower() in ("1", "true", "yes"):
        # Behind one reverse proxy: take the client address, scheme, host and prefix from its
        # X-Forwarded-* headers (so the cookie gets Secure over https and loopback checks see the real client).
        from werkzeug.middleware.proxy_fix import ProxyFix

        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

    from alfrd.gui.config import DefaultConfig

    DefaultConfig.apply(app)
    if config:
        app.config.update(config)

    # ``dict.setdefault`` does not replace Flask's initial ``None`` value.
    if not app.config.get("SECRET_KEY"):
        import secrets

        app.config["SECRET_KEY"] = secrets.token_hex(32)

    from alfrd.gui.auth import AlfrdSessionInterface, new_token

    # Access stays on by default: without a configured token one is generated
    # (nobody knows it, so only the public endpoints answer).
    if not app.config.get("ACCESS_TOKEN"):
        app.config["ACCESS_TOKEN"] = new_token()
    app.session_interface = AlfrdSessionInterface()

    from alfrd.gui.routes import api, control, dashboard, system
    from alfrd.gui.studio import studio, studio_api
    import alfrd.gui.studio_plans  # noqa: F401  (adds the plan routes to studio_api)
    import alfrd.gui.studio_targets  # noqa: F401  (adds the targets CSV routes to studio_api)
    import alfrd.gui.studio_collections  # noqa: F401  (adds the collection routes to studio_api)
    import alfrd.gui.studio_history  # noqa: F401  (adds the alfrd.yaml history routes to studio_api)
    import alfrd.gui.studio_search  # noqa: F401  (adds the full-text search routes to studio_api)
    import alfrd.gui.studio_notes  # noqa: F401  (adds the annotation routes to studio_api)
    import alfrd.gui.studio_views  # noqa: F401  (adds the template-driven view route to studio_api)

    from alfrd.gui.api_v1 import api_v1

    app.register_blueprint(studio)
    app.register_blueprint(studio_api)
    app.register_blueprint(api_v1)
    app.register_blueprint(api)
    app.register_blueprint(dashboard)
    app.register_blueprint(system)
    app.register_blueprint(control)

    from alfrd.gui.model import db
    # Register every model before create_all(), including in an installed wheel
    # where no test module has imported the table declarations first.
    from alfrd.gui.model import tables as _tables  # noqa: F401

    db.init_app(app)
    if app.config["CATALOG_CREATE_SCHEMA"]:
        with app.app_context():
            _ensure_catalog_schema(db)

    reader = app.config.get("CATALOG_READER")
    if reader is None:
        from alfrd.gui.services import SqlAlchemyCatalogReader

        reader = SqlAlchemyCatalogReader()
    app.extensions["alfrd_catalog_reader"] = reader

    from alfrd.gui.security import (
        csrf_token,
        mutations_enabled,
        protect_mutation,
        runtime_enabled,
    )

    from alfrd.gui.auth import require_access

    # Order matters: an unauthenticated POST gets 401 before the CSRF check's 403.
    app.before_request(require_access)
    app.before_request(protect_mutation)

    @app.context_processor
    def dashboard_runtime_context():
        return {
            "runtime_enabled": runtime_enabled(),
            "mutations_enabled": mutations_enabled(),
            "csrf_token": csrf_token(),
        }

    @app.errorhandler(HTTPException)
    def api_http_error(error):
        if request.path.startswith("/api/"):
            response = jsonify(
                error={"code": error.code, "message": error.description}
            )
            response.status_code = error.code
            # Keep WWW-Authenticate (401), Retry-After (429), Allow (405).
            for key, value in error.get_headers():
                if key.lower() != "content-type":
                    response.headers[key] = value
            return response
        return error

    return app


__all__ = ["create_app"]
