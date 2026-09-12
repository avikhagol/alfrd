def create_app(config=None):
    """Create the optional read-only catalog application.

    Flask remains an optional dependency: importing :mod:`alfrd` itself does
    not import this module.  ``CATALOG_READER`` may be supplied by an
    integration; otherwise the bundled SQLAlchemy metadata adapter is used.
    """
    from flask import Flask, jsonify, request
    from werkzeug.exceptions import HTTPException

    app = Flask(__name__)

    from alfrd.gui.config import DefaultConfig

    DefaultConfig.apply(app)
    if config:
        app.config.update(config)

    from alfrd.gui.routes import api, control, dashboard, system

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
            db.create_all()

    reader = app.config.get("CATALOG_READER")
    if reader is None:
        from alfrd.gui.services import SqlAlchemyCatalogReader

        reader = SqlAlchemyCatalogReader()
    app.extensions["alfrd_catalog_reader"] = reader

    @app.errorhandler(HTTPException)
    def api_http_error(error):
        if request.path.startswith("/api/"):
            return (
                jsonify(
                    error={"code": error.code, "message": error.description}
                ),
                error.code,
            )
        return error

    return app


__all__ = ["create_app"]