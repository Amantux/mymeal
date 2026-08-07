"""myMeal application factory.

A self-hosted recipe manager, AI meal planner, and cooking assistant. Ships an
optional-auth JSON API under ``/api/v1`` and serves the built Vue SPA. Designed
to run standalone or as a Home Assistant add-on.
"""
import logging
import json
import os
import re
import time
import uuid
from datetime import timedelta

from flask import Flask, g, jsonify, request, send_from_directory
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import safe_join

from .extensions import db, limiter
from .logging_setup import configure as configure_logging, request_id_var
from .logsafe import scrub
from .settings import FIELDS_BY_NAME, ensure_secret_key, load_settings

# Request ids appear in every log line for a request; keep them boring.
_SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]+")

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
    for path in (settings.data_dir, settings.images_dir, settings.videos_dir):
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
    # Not basicConfig: it silently does nothing once a handler exists, and
    # Alembic's fileConfig gets there first during migrations. configure()
    # installs our own handlers explicitly — including a file sink the app can
    # read back, since stdout is captured by the container, not by us.
    configure_logging(settings, process="app")
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
    # flask_limiter reads RATELIMIT_ENABLED from app.config (setdefault), so set
    # it BEFORE limiter.init_app — tests turn it off via the Config class.
    app.config["RATELIMIT_ENABLED"] = settings.RATELIMIT_ENABLED
    app.config["ALLOW_REGISTRATION"] = settings.ALLOW_REGISTRATION
    app.config["WORKER_ENABLED"] = settings.WORKER_ENABLED
    app.config["AI_CONFIDENCE_THRESHOLD"] = settings.AI_CONFIDENCE_THRESHOLD
    app.config["SQLALCHEMY_DATABASE_URI"] = settings.sqlalchemy_uri
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    # pool_pre_ping recycles connections dropped by a remote Postgres / network
    # (idle timeouts, restarts). Harmless for SQLite.
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_pre_ping": True,
        # ensure_ascii=False, or a JSON column stores "jalape\u00f1o" and the
        # lookup endpoint's cast-to-text ILIKE can never match what the user
        # types. The old CSV column matched raw text; this keeps that true.
        "json_serializer": lambda obj: json.dumps(obj, ensure_ascii=False),
    }
    app.config["images_dir"] = lambda: settings.images_dir
    app.config["videos_dir"] = lambda: settings.videos_dir
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
    limiter.init_app(app)

    from . import models  # noqa: F401  (register models)

    # Serialize schema init across gunicorn workers. Each worker builds its own
    # app and would otherwise run db.create_all() concurrently on a fresh DB —
    # create_all's check-then-create is not atomic across processes, so two
    # workers race and one dies with "table ... already exists". An exclusive
    # file lock lets the first worker create the schema; the rest then find it
    # already there and no-op.
    _init_schema(app, settings.data_dir)

    _register_blueprints(app)
    _register_spa(app)
    _register_request_context(app)
    _register_security_headers(app)
    _register_errors(app)

    from .worker import start_worker
    start_worker(app)  # background job poller (no-op when WORKER_ENABLED is False)
    return app


def _init_schema(app, data_dir):
    """Bring the schema to head via Alembic, under an exclusive file lock so
    concurrent gunicorn workers don't race on a fresh DB. Works on SQLite and
    Postgres alike."""
    import fcntl

    lock_path = os.path.join(data_dir, ".schema-init.lock")
    with open(lock_path, "w") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        except OSError:
            pass  # locking unsupported (rare FS) — Alembic is still safe to
                  # run; the lock only avoids redundant concurrent upgrades.
        with app.app_context():
            _run_migrations(app)


def _run_migrations(app):
    """Run Alembic migrations to head.

    Three cases, all handled:
      * Fresh DB → upgrade from nothing runs the baseline (create_all) + deltas.
      * Existing PRE-Alembic install (has tables, no ``alembic_version``) → it
        already has the baseline schema, so *stamp* baseline, then upgrade so any
        later deltas (e.g. the legacy pantry drop) still apply.
      * Already on Alembic → apply any pending revisions.
    """
    from alembic import command
    from alembic.config import Config as AlembicConfig
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import inspect

    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = AlembicConfig(os.path.join(backend_dir, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(backend_dir, "migrations"))
    # Pass the URL out-of-band via attributes, NOT set_main_option: Alembic's
    # Config is a ConfigParser, which would try to %-interpolate a URL-encoded
    # password (e.g. a `%40` for '@') and crash before the app can boot.
    cfg.attributes["url"] = app.config["SQLALCHEMY_DATABASE_URI"]

    with db.engine.connect() as conn:
        current = MigrationContext.configure(conn).get_current_revision()
    if current is None and inspect(db.engine).has_table("users"):
        # Tables exist but Alembic has never run here (a pre-Alembic install, or
        # a first boot interrupted mid-create — SQLite DDL is non-transactional,
        # so a partial schema is possible). Fill any gaps with a checkfirst
        # create_all, THEN adopt as the baseline so later migrations (0002+) run.
        db.create_all()
        command.stamp(cfg, "0001_baseline")
    command.upgrade(cfg, "head")


def _register_blueprints(app):
    from .api.ai import bp as ai_bp
    from .api.categories import bp as categories_bp
    from .api.chat import bp as chat_bp
    from .api.conversions import bp as conversions_bp
    from .api.edibl import bp as edibl_bp
    from .api.foods import bp as foods_bp
    from .api.groups import bp as groups_bp
    from .api.ha import bp as ha_bp
    from .api.jobs import bp as jobs_bp
    from .api.lookup import bp as lookup_bp
    from .api.mealplans import bp as mealplans_bp
    from .api.misc import bp as misc_bp
    from .api.preferences import bp as preferences_bp
    from .api.public import bp as public_bp
    from .api.recipe_versions import bp as recipe_versions_bp
    from .api.recipes import bp as recipes_bp
    from .api.shopping_lists import bp as shopping_lists_bp
    from .api.suggestions import bp as suggestions_bp
    from .api.tags import bp as tags_bp
    from .api.tokens import bp as tokens_bp
    from .api.users import bp as users_bp

    prefix = "/api/v1"
    for bp in (
        users_bp,
        groups_bp,
        tokens_bp,
        misc_bp,
        lookup_bp,
        recipes_bp,
        recipe_versions_bp,
        foods_bp,
        categories_bp,
        tags_bp,
        mealplans_bp,
        shopping_lists_bp,
        chat_bp,
        ha_bp,
        ai_bp,
        edibl_bp,
        preferences_bp,
        public_bp,
        jobs_bp,
        suggestions_bp,
        conversions_bp,
    ):
        app.register_blueprint(bp, url_prefix=prefix)


def _register_request_context(app):
    """Give every request a correlation id, and log the ones worth logging.

    The id is echoed as X-Request-Id and included in a 500 body, so a user can
    quote it in a bug report and it can be grepped straight out of the log.

    Only slow requests and server errors get a line: gunicorn's access log
    already records the ordinary ones, and writing a second line per request is
    how a household add-on fills its disk.
    """
    @app.before_request
    def _assign_request_id():
        incoming = request.headers.get("X-Request-Id", "")
        # Bounded and charset-checked: this value lands in every log line for
        # the request, so an attacker-supplied one must not be able to forge
        # log structure or bloat the file.
        if incoming and len(incoming) <= 64 and _SAFE_REQUEST_ID.fullmatch(incoming):
            rid = incoming
        else:
            rid = uuid.uuid4().hex[:12]
        request_id_var.set(rid)
        g._started_at = time.monotonic()

    @app.after_request
    def _finish_request(resp):
        resp.headers.setdefault("X-Request-Id", request_id_var.get())
        started = getattr(g, "_started_at", None)
        if started is None:
            return resp
        elapsed_ms = int((time.monotonic() - started) * 1000)
        threshold = app.config.get("SLOW_REQUEST_MS", 1000)
        if resp.status_code >= 500 or (threshold >= 0 and elapsed_ms >= threshold):
            logger.warning("%s %s -> %s in %dms",
                            scrub(request.method), scrub(request.path),
                            resp.status_code, elapsed_ms)
        return resp


def _register_security_headers(app):
    """Baseline hardening headers on every response.

    Ported from HomeHoard/Edibl, which have had these from the start — myMeal
    was the only one of the three shipping none at all.
    """
    from .auth import _request_from_ingress

    @app.after_request
    def _set_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        # 'unsafe-inline' for styles only — the app uses many inline style
        # attributes; scripts stay 'self'.
        csp = (
            "default-src 'self'; "
            # The ONLY third-party relaxation: the two hosts services/videos.py
            # will produce an embed URL for. Every other link opens in a new tab
            # rather than being framed, so no other origin is ever embedded.
            "frame-src https://www.youtube-nocookie.com https://player.vimeo.com; "
            "img-src 'self' data: blob:; "
            "media-src 'self' blob:; "
            "style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; "
            "connect-src 'self'; "
            "base-uri 'self'; "
            "form-action 'self'; "
            "object-src 'none'"
        )
        # Under HA ingress the app is legitimately framed by Home Assistant, so
        # anti-clickjacking is asserted only when the request is NOT from the
        # ingress peer. Keying on the per-request ingress check, not on auth
        # mode: disable_auth:false behind ingress is supported (ingress identity
        # is honoured even with auth on), and keying on auth mode there would
        # blank the HA panel. A non-ingress request always gets the headers.
        if not _request_from_ingress():
            csp += "; frame-ancestors 'self'"
            resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Content-Security-Policy", csp)
        # HSTS only over HTTPS (harmless/ignored over plain HTTP).
        if request.is_secure:
            resp.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return resp


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

    @app.errorhandler(Exception)
    def unhandled(e):
        """Log the traceback deliberately and return a curated body.

        Flask's default already logs unhandled exceptions, but only if logging
        happens to be configured — which it was not until this release. Doing it
        explicitly also lets us hand the caller the request id, so "it broke
        once" becomes a line an operator can actually find in the log.

        The exception text is NEVER returned: a driver error can carry the
        database password.
        """
        from werkzeug.exceptions import HTTPException

        if isinstance(e, HTTPException):
            return e  # 404/413/429 etc. keep their own handlers and status

        rid = request_id_var.get()
        logger.exception("unhandled error [%s] %s %s", rid,
                          scrub(request.method), scrub(request.path))
        return jsonify({"error": "internal server error", "requestId": rid}), 500


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
    # safe_join, not os.path.join: it rejects traversal ("../") and absolute
    # segments, returning None. os.path.join would happily build a path outside
    # the dist dir, letting the isfile() check probe for arbitrary files.
    # Nested asset paths ("assets/index-abc.js") are still served normally.
    full = safe_join(_FRONTEND_DIST, path) if path else None
    if full and os.path.isfile(full):
        return send_from_directory(_FRONTEND_DIST, path)
    index = os.path.join(_FRONTEND_DIST, "index.html")
    if os.path.isfile(index):
        return send_from_directory(_FRONTEND_DIST, "index.html")
    return (
        (
            "<h1>myMeal API</h1><p>Frontend not built. "
            "API is available under <code>/api/v1</code>.</p>"
        ),
        200,
    )


def _register_spa(app):
    @app.route("/", defaults={"path": ""})
    @app.route("/<path:path>")
    def spa(path):
        if path.startswith("api/"):
            return jsonify({"error": "not found"}), 404
        return _serve_spa(path)
