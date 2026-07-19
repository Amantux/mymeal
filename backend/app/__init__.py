"""myMeal application factory.

A self-hosted recipe manager, AI meal planner, and cooking assistant. Ships an
optional-auth JSON API under ``/api/v1`` and serves the built Vue SPA. Designed
to run standalone or as a Home Assistant add-on.
"""
import os

from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS

from .config import Config
from .extensions import db


def create_app(config_object=Config):
    app = Flask(__name__, static_folder=None)
    app.config.from_object(config_object)
    app.config["SQLALCHEMY_DATABASE_URI"] = config_object.sqlalchemy_uri()
    app.config["images_dir"] = config_object.images_dir
    app.config["MAX_CONTENT_LENGTH"] = config_object.MAX_UPLOAD_BYTES

    CORS(app, supports_credentials=True)
    db.init_app(app)

    from . import models  # noqa: F401  (register models)

    with app.app_context():
        db.create_all()
        _migrate(app)

    _register_blueprints(app)
    _register_spa(app)
    _register_errors(app)
    return app


def _migrate(app):
    """Additive schema migrations for existing SQLite databases.

    ``db.create_all`` never alters existing tables, so add any columns that
    were introduced after a database was first created. Empty today; new
    columns get an entry here (``table -> {column: DDL}``) as the schema grows.
    """
    from sqlalchemy import text, inspect

    if not db.engine.url.get_backend_name().startswith("sqlite"):
        return
    inspector = inspect(db.engine)
    wanted: dict[str, dict[str, str]] = {}
    for table, columns in wanted.items():
        if not inspector.has_table(table):
            continue
        existing = {c["name"] for c in inspector.get_columns(table)}
        with db.engine.begin() as conn:
            for name, ddl in columns.items():
                if name not in existing:
                    conn.execute(
                        text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
                    )


def _register_blueprints(app):
    from .api.users import bp as users_bp
    from .api.groups import bp as groups_bp
    from .api.tokens import bp as tokens_bp
    from .api.misc import bp as misc_bp
    from .api.lookup import bp as lookup_bp
    from .api.recipes import bp as recipes_bp
    from .api.foods import bp as foods_bp
    from .api.categories import bp as categories_bp
    from .api.tags import bp as tags_bp
    from .api.mealplans import bp as mealplans_bp
    from .api.pantry import bp as pantry_bp
    from .api.shopping_lists import bp as shopping_lists_bp
    from .api.ai import bp as ai_bp

    prefix = "/api/v1"
    for bp in (
        users_bp,
        groups_bp,
        tokens_bp,
        misc_bp,
        lookup_bp,
        recipes_bp,
        foods_bp,
        categories_bp,
        tags_bp,
        mealplans_bp,
        pantry_bp,
        shopping_lists_bp,
        ai_bp,
    ):
        app.register_blueprint(bp, url_prefix=prefix)


def _register_errors(app):
    @app.errorhandler(404)
    def not_found(e):
        from flask import request

        if request.path.startswith("/api/"):
            return jsonify({"error": "not found"}), 404
        return _serve_spa("index.html")

    @app.errorhandler(413)
    def too_large(e):
        return jsonify({"error": "upload too large"}), 413


# --- SPA serving ---------------------------------------------------------
_FRONTEND_DIST = os.environ.get(
    "MYMEAL_FRONTEND_DIST",
    os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist")
    ),
)


def _serve_spa(path):
    full = os.path.join(_FRONTEND_DIST, path)
    if path and os.path.isfile(full):
        return send_from_directory(_FRONTEND_DIST, path)
    index = os.path.join(_FRONTEND_DIST, "index.html")
    if os.path.isfile(index):
        return send_from_directory(_FRONTEND_DIST, "index.html")
    return (
        "<h1>myMeal API</h1><p>Frontend not built. "
        "API is available under <code>/api/v1</code>.</p>",
        200,
    )


def _register_spa(app):
    @app.route("/", defaults={"path": ""})
    @app.route("/<path:path>")
    def spa(path):
        if path.startswith("api/"):
            return jsonify({"error": "not found"}), 404
        return _serve_spa(path)
