"""myMeal application factory.

A self-hosted recipe manager, AI meal planner, and cooking assistant. Ships an
optional-auth JSON API under ``/api/v1`` and serves the built Vue SPA. Designed
to run standalone or as a Home Assistant add-on.
"""
import logging
import os
from datetime import timedelta

from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix

from .extensions import db
from .settings import FIELDS_BY_NAME, ensure_secret_key, load_settings

logger = logging.getLogger(__name__)


def _settings_from(config_object):
    """Resolve settings for this app instance.

    ``config_object`` keeps the historical ``create_app(TestConfig)`` contract
    working: any attribute on it that names a known setting becomes an explicit
    override, which sits at the top of the precedence chain. Each call resolves
    independently, so one process can build many differently-configured apps —
    previously impossible, because ``Config`` captured ``os.environ`` at import
    time and the first import won forever.
    """
    if config_object is None:
        return load_settings()

    overrides = {}
    for name in FIELDS_BY_NAME:
        if hasattr(config_object, name):
            overrides[name] = getattr(config_object, name)
    # Historical spellings that do not map 1:1 onto a field name.
    if hasattr(config_object, "MAX_UPLOAD_BYTES") and "MAX_UPLOAD_MB" not in overrides:
        overrides["MAX_UPLOAD_MB"] = max(1, int(config_object.MAX_UPLOAD_BYTES) // (1024 * 1024))
    # A test config is by definition not a production deployment; do not demand
    # a 32-character signing secret from a fixture.
    return load_settings(overrides=overrides, ha_options={}, strict_secret=False)


def _prepare_storage(settings):
    """Create the data directories deliberately, with actionable errors.

    Directory creation used to happen as a side effect of reading a config
    property, which meant merely importing the wrong module could scatter a
    ``data/`` directory into whatever the current working directory happened
    to be.
    """
    for path in (settings.data_dir, settings.images_dir):
        try:
            os.makedirs(path, exist_ok=True)
        except OSError as exc:
            raise RuntimeError(
                f"Cannot create data directory {path!r}: {exc}. "
                f"Set MYMEAL_DATA_DIR to a writable location, or fix ownership "
                f"of the mounted volume."
            ) from exc
    if not os.access(settings.data_dir, os.W_OK):
        raise RuntimeError(
            f"Data directory {settings.data_dir!r} is not writable by this process "
            f"(uid {os.getuid()}). myMeal stores its database and uploaded images "
            f"here, so it cannot start."
        )


def _log_startup(settings):
    """One structured startup block. Deliberately contains no secret values.

    Everything here is something an operator needs to confirm they got the
    deployment they intended — the questions that otherwise get answered by
    reading source code.
    """
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    db_kind = "sqlite" if settings.sqlalchemy_uri.startswith("sqlite") else \
        settings.sqlalchemy_uri.split("://", 1)[0]
    logger.info(
        "myMeal starting | mode=%s auth=%s registration=%s db=%s data_dir=%s "
        "ai=%s mcp=%s workers=%s",
        "home-assistant" if settings.sources.get("DISABLE_AUTH") == "ha_option" else "standalone",
        "disabled" if settings.DISABLE_AUTH else "jwt",
        "open" if settings.ALLOW_REGISTRATION else "closed",
        db_kind,
        settings.data_dir,
        settings.AI_PROVIDER or "disabled",
        f"port {settings.MCP_PORT}" if settings.MCP_ENABLED else "disabled",
        settings.WORKERS,
    )
    for warning in settings.warnings:
        logger.warning("config: %s", warning)


def create_app(config_object=None):
    settings = _settings_from(config_object)
    _prepare_storage(settings)

    secret, generated = ensure_secret_key(settings.values, settings.data_dir)
    if generated:
        logger.warning(
            "No MYMEAL_SECRET_KEY was supplied; generated one and persisted it to "
            "%s so sessions survive restarts. Back this file up with your data.",
            os.path.join(settings.data_dir, ".secret_key"),
        )

    app = Flask(__name__, static_folder=None)
    app.config["SETTINGS"] = settings
    app.config["SECRET_KEY"] = secret
    app.config["JWT_EXPIRES"] = timedelta(hours=settings.JWT_HOURS)
    app.config["DISABLE_AUTH"] = settings.DISABLE_AUTH
    app.config["ALLOW_REGISTRATION"] = settings.ALLOW_REGISTRATION
    app.config["SQLALCHEMY_DATABASE_URI"] = settings.sqlalchemy_uri
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["images_dir"] = lambda: settings.images_dir
    app.config["MAX_CONTENT_LENGTH"] = settings.MAX_UPLOAD_MB * 1024 * 1024
    app.config["JSON_SORT_KEYS"] = False
    app.config["DEBUG"] = settings.DEBUG

    # Trust X-Forwarded-* only for the number of proxies the operator declares.
    # Accepting them from arbitrary clients lets a caller forge their apparent
    # scheme and address, so the default (0) trusts nothing.
    if settings.TRUSTED_PROXY_COUNT:
        n = settings.TRUSTED_PROXY_COUNT
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=n, x_proto=n, x_host=n)

    # Same-origin by default: the SPA is served by this very app, so no
    # cross-origin access is required. Previously this was
    # ``CORS(app, supports_credentials=True)`` with no origin list, which
    # reflects ANY origin — allowing any website to make credentialed calls.
    if settings.CORS_ORIGINS:
        CORS(app, resources={r"/api/*": {"origins": list(settings.CORS_ORIGINS)}},
             supports_credentials=True)

    _log_startup(settings)
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

    # DESTRUCTIVE, one-way: myMeal no longer owns a pantry — inventory is owned
    # by the companion Edibl app. Drop the legacy table if an older install has
    # it. Explicitly requested; the data is superseded by Edibl. Scoped to this
    # one table by name so nothing else can be affected, and idempotent
    # (DROP ... IF EXISTS) so it is a no-op on new databases and on re-runs.
    if inspector.has_table("pantry_items"):
        with db.engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS pantry_items"))
        logger.warning(
            "Dropped the legacy 'pantry_items' table: myMeal's pantry moved to "
            "the Edibl integration. Any rows it held are gone."
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
    from .api.shopping_lists import bp as shopping_lists_bp
    from .api.chat import bp as chat_bp
    from .api.ha import bp as ha_bp
    from .api.ai import bp as ai_bp
    from .api.edibl import bp as edibl_bp

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
        shopping_lists_bp,
        chat_bp,
        ha_bp,
        ai_bp,
        edibl_bp,
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
_DEFAULT_FRONTEND_DIST = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist")
)


def _frontend_dist():
    """Resolved per-request from this app's settings rather than captured at
    import time, so two apps in one process can serve different builds."""
    from flask import current_app
    settings = current_app.config.get("SETTINGS")
    configured = getattr(settings, "FRONTEND_DIST", "") if settings else ""
    return configured or _DEFAULT_FRONTEND_DIST


def _serve_spa(path):
    _FRONTEND_DIST = _frontend_dist()
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
