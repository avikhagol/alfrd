from alfrd import get_alfrd_dir

def create_app(config=None):
    """Create the optional Flask application without importing Flask at package import."""
    from flask import Flask

    app = Flask(__name__)
    
    db_path = get_alfrd_dir() / "alfrd.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"
    if config:
        app.config.update(config)
    
    from alfrd.gui.routes import api, dashboard
    app.register_blueprint(api)
    app.register_blueprint(dashboard)
    
    from alfrd.gui.model import db
    db.init_app(app)
    
    with app.app_context():
        db.create_all()
    
    return app


__all__ = ["create_app"]