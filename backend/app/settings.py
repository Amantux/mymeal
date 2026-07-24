"""The single configuration contract for myMeal.

Every setting the application understands is declared exactly once, in
``FIELDS`` below. Nothing else in the codebase may read ``os.environ`` for a
``MYMEAL_`` value — the inventory, the docs, the ``config_check`` CLI and the
CI documentation test all derive from this one table, so they cannot drift
apart the way scattered ``os.environ.get`` calls did.

Precedence (highest wins)
-------------------------
1. **Explicit overrides** passed to ``load_settings(overrides=...)`` — used by
   ``create_app(SomeConfig)`` and by tests, which must be able to build several
   differently-configured apps in one process.
2. **Home Assistant add-on options** (``/data/options.json``), when present.
3. **Environment variables**.
4. **Declared defaults** in ``FIELDS``.

Why HA options outrank the environment — this is the one non-obvious choice.
Inside the add-on, options.json is the *only* surface an operator can edit; the
environment is baked into the image and Supervisor. If the environment won,
toggling "disable_auth" in the HA UI would silently do nothing, which is a
worse failure than the reverse. Outside HA the file does not exist, so the
environment is authoritative there. This makes the pre-existing entrypoint
behaviour explicit and testable rather than accidental.

Resolution is a pure function of its inputs: no import-time capture, no
``os.environ`` reads at class-definition time, and no directory creation as a
side effect of importing a module.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import stat
from dataclasses import dataclass, field
from typing import Any, Callable

# Settings the operator must never see echoed back, in logs or diagnostics.
SECRET_FIELDS = frozenset({
    "SECRET_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "MCP_API_TOKEN",
    "MCP_SERVER_TOKEN",
})

PLACEHOLDER_SECRETS = frozenset({
    "change-me-in-production", "change-me", "changeme", "secret", "password",
    "test", "dev", "development", "please-change-me",
})

# Substrings that mark a value as an example rather than a real secret. Exact
# matching is not enough: the shipped compose file said
# "please-change-me-to-a-long-random-string", which is long enough to pass a
# length check and would otherwise have been accepted as a production secret.
PLACEHOLDER_MARKERS = ("change-me", "changeme", "change_me", "example",
                       "placeholder", "your-secret", "yoursecret", "insecure",
                       "replace-me", "notasecret", "not-a-secret")

MIN_SECRET_LENGTH = 32


def _is_placeholder(value: str) -> bool:
    v = value.strip().lower()
    return v in PLACEHOLDER_SECRETS or any(m in v for m in PLACEHOLDER_MARKERS)

REDACTED = "***redacted***"


class ConfigError(Exception):
    """Raised when configuration is invalid. Carries every problem at once.

    Reporting one error per run turns fixing a misconfigured deployment into a
    guessing game, so validation collects all of them before raising.
    """

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("Invalid configuration:\n" + "\n".join(f"  - {e}" for e in errors))


# --------------------------------------------------------------------------
# Parsers. Each raises ValueError with a message naming the accepted values —
# silently coercing an unknown value to a default is how a production system
# ends up with auth disabled because someone wrote "MYMEAL_DISABLE_AUTH=True ".
# --------------------------------------------------------------------------

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def parse_bool(raw: str) -> bool:
    v = str(raw).strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    raise ValueError(
        f"expected a boolean, got {raw!r}. Accepted: "
        f"{', '.join(sorted(_TRUE))} (true) / {', '.join(sorted(_FALSE))} (false)"
    )


def int_between(low: int, high: int) -> Callable[[str], int]:
    def parse(raw: str) -> int:
        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError):
            raise ValueError(f"expected an integer, got {raw!r}")
        if not (low <= value <= high):
            raise ValueError(f"expected an integer between {low} and {high}, got {value}")
        return value
    return parse


def one_of(*allowed: str) -> Callable[[str], str]:
    def parse(raw: str) -> str:
        v = str(raw).strip()
        if v not in allowed:
            shown = ", ".join(repr(a) if a else "'' (disabled)" for a in allowed)
            raise ValueError(f"expected one of {shown}, got {v!r}")
        return v
    return parse


def http_url(raw: str) -> str:
    v = str(raw).strip().rstrip("/")
    if not re.match(r"^https?://[^\s/]+", v):
        raise ValueError(f"expected an http(s) URL, got {raw!r}")
    return v


def csv_list(raw: str) -> tuple[str, ...]:
    return tuple(p.strip() for p in str(raw).split(",") if p.strip())


def as_str(raw: str) -> str:
    return str(raw)


@dataclass(frozen=True)
class Field:
    name: str                      # without the MYMEAL_ prefix
    parse: Callable[[str], Any]
    default: Any
    doc: str
    secret: bool = False
    ha_option: str | None = None   # matching key in options.json
    restart_required: bool = True
    supports_file: bool = False    # honours MYMEAL_<NAME>_FILE (Docker secrets)

    @property
    def env_var(self) -> str:
        return f"MYMEAL_{self.name}"


# --------------------------------------------------------------------------
# THE INVENTORY. Adding a setting anywhere else is a bug that CI will catch.
# --------------------------------------------------------------------------
FIELDS: tuple[Field, ...] = (
    # --- storage ---
    Field("DATA_DIR", as_str, "./data",
          "Directory for the SQLite database and uploaded images."),
    Field("DATABASE_URL", as_str, "",
          "Full SQLAlchemy URL. Blank = SQLite inside DATA_DIR. Postgres is "
          "supported: postgresql+psycopg://user:pass@host:5432/dbname.",
          secret=True, ha_option="database_url", supports_file=True),

    # --- security ---
    Field("SECRET_KEY", as_str, "",
          "JWT signing key. Blank = generated once and persisted in DATA_DIR.",
          secret=True, supports_file=True),
    Field("JWT_HOURS", int_between(1, 24 * 365), 168,
          "How long an issued token stays valid, in hours."),
    Field("DISABLE_AUTH", parse_bool, False,
          "Bind every request to a single local user. ONLY safe behind an "
          "authenticating proxy such as Home Assistant ingress.",
          ha_option="disable_auth"),
    Field("ALLOW_REGISTRATION", parse_bool, True,
          "Allow anyone who can reach the app to create an account.",
          ha_option="allow_registration"),

    # --- network / proxy ---
    Field("PORT", int_between(1, 65535), 7850, "HTTP port for the app."),
    Field("CORS_ORIGINS", csv_list, (),
          "Comma-separated origins allowed to make credentialed cross-origin "
          "requests. Empty = same-origin only (correct for normal deployments)."),
    Field("TRUSTED_PROXY_COUNT", int_between(0, 10), 0,
          "How many reverse proxies sit in front. 0 = do not trust "
          "X-Forwarded-* headers at all."),

    # --- AI ---
    Field("AI_PROVIDER", one_of("", "claude", "ollama", "openai"), "",
          "Which AI backend to use. Blank disables AI features cleanly.",
          ha_option="ai_provider"),
    Field("ANTHROPIC_API_KEY", as_str, "", "API key for the claude provider.",
          secret=True, supports_file=True, ha_option="anthropic_api_key"),
    Field("CLAUDE_MODEL", as_str, "claude-opus-4-8", "Model for the claude provider.",
          ha_option="claude_model"),
    Field("OPENAI_API_KEY", as_str, "", "API key for the openai provider.",
          secret=True, supports_file=True, ha_option="openai_api_key"),
    Field("OPENAI_MODEL", as_str, "gpt-4o-mini", "Model for the openai provider.",
          ha_option="openai_model"),
    Field("OPENAI_BASE_URL", as_str, "",
          "Override the OpenAI API base URL (for compatible gateways)."),
    Field("OLLAMA_HOST", http_url, "http://localhost:11434",
          "Base URL of the Ollama server. Local, so no API key is needed.",
          ha_option="ollama_host"),
    Field("OLLAMA_MODEL", as_str, "llama3.1", "Model for the ollama provider.",
          ha_option="ollama_model"),
    Field("AI_TIMEOUT_SECONDS", int_between(1, 600), 60,
          "Per-request timeout for AI provider calls."),

    # --- Edibl (sibling food-inventory app) ---
    Field("EDIBL_URL", as_str, "",
          "Base URL of a companion Edibl instance (e.g. http://edibl:7746). "
          "Blank disables the integration. When set, myMeal can pull real stock "
          "and push meal-plan ingredients. Can also be set in the UI (remembered).",
          ha_option="edibl_url"),
    Field("EDIBL_API_TOKEN", as_str, "",
          "API token myMeal presents to Edibl (Edibl tokens API). Sent as a "
          "Bearer token. Not needed when Edibl runs behind HA ingress with auth "
          "disabled.", secret=True, supports_file=True, ha_option="edibl_token"),

    # --- MCP ---
    Field("MCP_ENABLED", parse_bool, True,
          "Run the MCP server so Home Assistant Assist can use myMeal as a tool.",
          ha_option="enable_mcp"),
    Field("MCP_HOST", as_str, "0.0.0.0", "Bind address for the MCP server."),
    Field("MCP_PORT", int_between(1, 65535), 7851, "Port for the MCP server (SSE)."),
    Field("MCP_API", as_str, "",
          "Backend API URL the MCP server calls. Blank = derived from PORT."),
    Field("MCP_API_TOKEN", as_str, "",
          "Token the MCP server uses when app auth is enabled.",
          secret=True, supports_file=True),
    Field("MCP_SERVER_TOKEN", as_str, "",
          "Bearer token Home Assistant must present to reach the MCP server. "
          "Blank leaves it unauthenticated (only safe on a trusted network).",
          ha_option="mcp_server_token", secret=True, supports_file=True),
    Field("MCP_REQUIRED", parse_bool, False,
          "Treat a dead MCP process as making the whole app UNREADY."),

    # --- serving ---
    Field("WORKERS", int_between(1, 32), 2,
          "Gunicorn worker processes. SQLite is single-writer: more workers "
          "raises write contention, it does not raise write throughput."),
    Field("THREADS", int_between(1, 64), 4, "Gunicorn threads per worker."),
    Field("TIMEOUT", int_between(5, 3600), 120, "Gunicorn worker timeout (seconds)."),
    Field("GRACEFUL_TIMEOUT", int_between(1, 3600), 30,
          "Seconds to finish in-flight requests on shutdown."),
    Field("MAX_UPLOAD_MB", int_between(1, 1024), 50, "Maximum upload size in MB."),
    Field("HTTP_TIMEOUT_SECONDS", int_between(1, 300), 20,
          "Timeout for outbound HTTP (recipe import)."),
    Field("MAX_IMPORT_BYTES", int_between(1024, 100 * 1024 * 1024), 5 * 1024 * 1024,
          "Maximum bytes read from a page during recipe import."),

    # --- misc ---
    Field("FRONTEND_DIST", as_str, "",
          "Path to the built SPA. Blank = the location baked into the image."),
    Field("LOG_LEVEL", one_of("DEBUG", "INFO", "WARNING", "ERROR"), "INFO",
          "Application log level."),
    Field("DEBUG", parse_bool, False,
          "Flask debug mode. NEVER enable in production — it exposes an "
          "interactive debugger that executes arbitrary code."),
)

FIELDS_BY_NAME = {f.name: f for f in FIELDS}


@dataclass(frozen=True)
class Settings:
    """Resolved, validated configuration. Immutable after startup."""

    values: dict[str, Any]
    warnings: tuple[str, ...] = ()
    sources: dict[str, str] = field(default_factory=dict)

    def __getattr__(self, item: str) -> Any:
        try:
            return object.__getattribute__(self, "values")[item]
        except KeyError:
            raise AttributeError(item)

    def __getitem__(self, item: str) -> Any:
        return self.values[item]

    # -- derived -----------------------------------------------------------
    @property
    def data_dir(self) -> str:
        return os.path.abspath(self.values["DATA_DIR"])

    @property
    def images_dir(self) -> str:
        return os.path.join(self.data_dir, "images")

    @property
    def sqlalchemy_uri(self) -> str:
        if self.values["DATABASE_URL"]:
            return self.values["DATABASE_URL"]
        return f"sqlite:///{os.path.join(self.data_dir, 'mymeal.db')}"

    @property
    def mcp_api(self) -> str:
        return self.values["MCP_API"] or f"http://127.0.0.1:{self.values['PORT']}/api/v1"

    @property
    def ai_enabled(self) -> bool:
        return bool(self.values["AI_PROVIDER"])

    @property
    def edibl_enabled(self) -> bool:
        return bool(self.values["EDIBL_URL"])

    def redacted(self) -> dict[str, Any]:
        """Effective settings with every secret replaced. Safe to print/log."""
        out: dict[str, Any] = {}
        for f in FIELDS:
            value = self.values[f.name]
            if f.secret and value:
                value = REDACTED
            elif isinstance(value, tuple):
                value = list(value)
            out[f.name] = value
        return out


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------

def load_ha_options(path: str = "/data/options.json") -> dict[str, Any]:
    """Read Home Assistant add-on options. Parsed ONCE, here, and nowhere else.

    A malformed file is reported rather than swallowed: an add-on that silently
    ignores its own options is indistinguishable from one that is broken.
    """
    if not os.path.isfile(path):
        return {}
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError([f"{path} could not be read as JSON: {exc}"])
    if not isinstance(data, dict):
        raise ConfigError([f"{path} must contain a JSON object, got {type(data).__name__}"])
    return data


def _read_secret_file(path: str, name: str, errors: list[str]) -> str | None:
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError as exc:
        errors.append(f"{name}: cannot read secret file {path!r}: {exc}")
        return None


def load_settings(
    env: dict[str, str] | None = None,
    overrides: dict[str, Any] | None = None,
    ha_options: dict[str, Any] | None = None,
    ha_options_path: str = "/data/options.json",
    strict_secret: bool | None = None,
) -> Settings:
    """Resolve settings from all sources. Pure: no I/O beyond reading inputs.

    Raises ConfigError listing EVERY problem found, so a misconfigured deploy
    is fixed in one pass rather than one error at a time.
    """
    env = os.environ if env is None else env
    overrides = overrides or {}
    if ha_options is None:
        ha_options = load_ha_options(ha_options_path)
    in_ha = bool(ha_options)

    errors: list[str] = []
    warnings: list[str] = []
    values: dict[str, Any] = {}
    sources: dict[str, str] = {}

    for f in FIELDS:
        raw: Any = None
        source = "default"

        # 4. defaults  <  3. environment  <  2. HA options  <  1. overrides
        if f.supports_file and env.get(f.env_var + "_FILE"):
            content = _read_secret_file(env[f.env_var + "_FILE"], f.env_var, errors)
            if content is not None:
                raw, source = content, "file"
        if raw is None and f.env_var in env and env[f.env_var] != "":
            raw, source = env[f.env_var], "env"
        if f.ha_option and f.ha_option in ha_options:
            ha_raw = ha_options[f.ha_option]
            # An empty string in options.json means "not set" (the HA UI writes
            # "" for a cleared optional field), so it must not beat a default.
            if ha_raw != "" or f.default == "":
                raw, source = ha_raw, "ha_option"
        if f.name in overrides:
            raw, source = overrides[f.name], "override"

        if raw is None:
            values[f.name] = f.default
            sources[f.name] = "default"
            continue

        # Overrides may pass already-typed values (a test passing DEBUG=True).
        if source == "override" and not isinstance(raw, str):
            values[f.name] = raw
            sources[f.name] = source
            continue
        # options.json is typed JSON: a real bool stays a bool.
        if source == "ha_option" and isinstance(raw, bool) and f.parse is parse_bool:
            values[f.name] = raw
            sources[f.name] = source
            continue

        try:
            values[f.name] = f.parse(raw)
            sources[f.name] = source
        except ValueError as exc:
            shown = REDACTED if f.secret else repr(raw)
            errors.append(f"{f.env_var} (from {source}, value {shown}): {exc}")
            values[f.name] = f.default
            sources[f.name] = "default"

    if errors:
        raise ConfigError(errors)

    _validate_semantics(values, sources, in_ha, errors, warnings, strict_secret)
    if errors:
        raise ConfigError(errors)

    return Settings(values=values, warnings=tuple(warnings), sources=sources)


def _validate_semantics(values, sources, in_ha, errors, warnings, strict_secret) -> None:
    """Cross-field rules. This is where unsafe COMBINATIONS are caught.

    Individually valid settings can combine into an unambiguously unsafe
    deployment; those fail closed. Suspicious-but-legitimate ones warn.
    """
    # --- the signing secret ---
    secret = values["SECRET_KEY"]
    # Default to strict whenever we are not obviously a throwaway/test app.
    strict = (not in_ha) if strict_secret is None else strict_secret
    if secret:
        if _is_placeholder(secret):
            errors.append(
                "MYMEAL_SECRET_KEY is a known placeholder value. Generate one with: "
                "python3 -c 'import secrets;print(secrets.token_urlsafe(32))'"
            )
        elif len(secret) < MIN_SECRET_LENGTH and strict and not values["DISABLE_AUTH"]:
            errors.append(
                f"MYMEAL_SECRET_KEY is only {len(secret)} characters; at least "
                f"{MIN_SECRET_LENGTH} are required when authentication is enabled."
            )

    # --- auth mode ---
    if values["DISABLE_AUTH"]:
        if values["ALLOW_REGISTRATION"]:
            warnings.append(
                "DISABLE_AUTH is on, so ALLOW_REGISTRATION has no effect "
                "(every request is already the same local user)."
            )
        if not in_ha and values["TRUSTED_PROXY_COUNT"] == 0:
            warnings.append(
                "DISABLE_AUTH is on outside Home Assistant with TRUSTED_PROXY_COUNT=0. "
                "Every request will be treated as an authenticated local user. This is "
                "only safe if something in front of myMeal authenticates callers — set "
                "MYMEAL_TRUSTED_PROXY_COUNT to confirm a proxy is present."
            )
        if values["CORS_ORIGINS"]:
            errors.append(
                "DISABLE_AUTH=true together with MYMEAL_CORS_ORIGINS is unsafe: it lets "
                "another website drive this API as the local user. Remove CORS_ORIGINS "
                "or enable authentication."
            )

    # --- debug ---
    if values["DEBUG"]:
        if in_ha:
            errors.append("MYMEAL_DEBUG cannot be enabled in the Home Assistant add-on.")
        else:
            warnings.append(
                "MYMEAL_DEBUG is on. The Werkzeug debugger executes arbitrary code — "
                "never expose this to an untrusted network."
            )

    # --- CORS ---
    if "*" in values["CORS_ORIGINS"]:
        errors.append(
            "MYMEAL_CORS_ORIGINS may not contain '*': credentialed requests from any "
            "origin would be permitted. List explicit origins instead."
        )
    for origin in values["CORS_ORIGINS"]:
        if not re.match(r"^https?://", origin):
            errors.append(f"MYMEAL_CORS_ORIGINS entry {origin!r} must start with http:// or https://")

    # --- AI provider coherence ---
    provider = values["AI_PROVIDER"]
    if provider == "claude" and not values["ANTHROPIC_API_KEY"]:
        warnings.append(
            "AI_PROVIDER=claude but MYMEAL_ANTHROPIC_API_KEY is not set. AI features "
            "will report as unavailable; the rest of the app is unaffected."
        )
    if provider == "openai" and not values["OPENAI_API_KEY"]:
        warnings.append(
            "AI_PROVIDER=openai but MYMEAL_OPENAI_API_KEY is not set. AI features "
            "will report as unavailable; the rest of the app is unaffected."
        )

    # --- serving ---
    uri = values["DATABASE_URL"]
    is_sqlite = (not uri) or uri.startswith("sqlite")
    if is_sqlite and values["WORKERS"] > 4:
        warnings.append(
            f"WORKERS={values['WORKERS']} with SQLite. SQLite serialises writes, so "
            "extra workers add lock contention rather than write throughput. "
            "Prefer raising THREADS."
        )
    if uri and not is_sqlite and "postgresql" not in uri.split(":")[0]:
        warnings.append(
            f"MYMEAL_DATABASE_URL points at {uri.split(':')[0]!r}. Only SQLite "
            "(default) and Postgres (postgresql+psycopg://…) are supported."
        )

    edibl_url = values["EDIBL_URL"]
    if edibl_url and not re.match(r"^https?://", edibl_url):
        errors.append(
            f"MYMEAL_EDIBL_URL must start with http:// or https://, got {edibl_url!r}"
        )
    if values["EDIBL_API_TOKEN"] and not edibl_url:
        warnings.append(
            "MYMEAL_EDIBL_API_TOKEN is set but MYMEAL_EDIBL_URL is not, so the "
            "Edibl integration is inactive."
        )

    if values["MCP_ENABLED"] and values["MCP_PORT"] == values["PORT"]:
        errors.append(
            f"MYMEAL_MCP_PORT and MYMEAL_PORT are both {values['PORT']}; "
            "the MCP server and the web app cannot share a port."
        )


# --------------------------------------------------------------------------
# Secret persistence
# --------------------------------------------------------------------------

def ensure_secret_key(settings_values: dict[str, Any], data_dir: str) -> tuple[str, bool]:
    """Return (secret, was_generated), persisting a generated one.

    A signing key regenerated on every restart logs every user out and voids
    every issued API token — a silent, confusing failure. If the operator does
    not supply one we generate it ONCE and persist it beside the database, so
    restarts are non-events.
    """
    if settings_values.get("SECRET_KEY"):
        return settings_values["SECRET_KEY"], False

    path = os.path.join(data_dir, ".secret_key")
    if os.path.isfile(path):
        with open(path) as fh:
            existing = fh.read().strip()
        if existing:
            return existing, False

    generated = secrets.token_urlsafe(48)
    os.makedirs(data_dir, exist_ok=True)
    with open(path, "w") as fh:
        fh.write(generated)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0600 — owner only
    return generated, True
