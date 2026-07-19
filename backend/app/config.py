"""Application configuration.

myMeal is a self-hosted recipe manager, AI meal planner, and cooking assistant.
Configuration is driven by environment variables so it can run standalone or as
a Home Assistant add-on.
"""
import os
from datetime import timedelta


def _bool(name: str, default: bool = False) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    # --- Storage ---------------------------------------------------------
    DATA_DIR = os.environ.get("MYMEAL_DATA_DIR", os.path.abspath("./data"))
    DATABASE_URL = os.environ.get("MYMEAL_DATABASE_URL")

    # --- Security --------------------------------------------------------
    SECRET_KEY = os.environ.get("MYMEAL_SECRET_KEY", "change-me-in-production")
    JWT_EXPIRES = timedelta(hours=int(os.environ.get("MYMEAL_JWT_HOURS", "168")))

    # When auth is disabled the app runs single-tenant against a default
    # user/group. Intended for deployment behind Home Assistant ingress,
    # which already enforces authentication.
    DISABLE_AUTH = _bool("MYMEAL_DISABLE_AUTH", False)

    # Allow public self-registration of new users/groups.
    ALLOW_REGISTRATION = _bool("MYMEAL_ALLOW_REGISTRATION", True)

    # --- AI --------------------------------------------------------------
    # Provider is pluggable: "claude" | "ollama" | "openai" (see services/ai).
    # Empty means AI features are disabled until configured in Settings.
    AI_PROVIDER = os.environ.get("MYMEAL_AI_PROVIDER", "")

    # --- Misc ------------------------------------------------------------
    MAX_UPLOAD_BYTES = int(os.environ.get("MYMEAL_MAX_UPLOAD_MB", "50")) * 1024 * 1024
    JSON_SORT_KEYS = False

    @classmethod
    def sqlalchemy_uri(cls) -> str:
        if cls.DATABASE_URL:
            return cls.DATABASE_URL
        os.makedirs(cls.DATA_DIR, exist_ok=True)
        return f"sqlite:///{os.path.join(cls.DATA_DIR, 'mymeal.db')}"

    @classmethod
    def images_dir(cls) -> str:
        path = os.path.join(cls.DATA_DIR, "images")
        os.makedirs(path, exist_ok=True)
        return path
