"""Long-lived API tokens (API keys).

Unlike the short-lived login JWTs, these do not expire and are meant for
machine clients — most notably the Home Assistant integration and the MCP
server polling ``/ha/summary`` and ``/search`` when app auth is enabled. Only a
SHA-256 hash is stored; the raw token is shown to the user exactly once at
creation.
"""
import hashlib
import secrets

from sqlalchemy import String, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from .base import IDMixin, TimestampMixin

# Raw tokens carry this prefix so the auth layer can tell them apart from a JWT
# without a database round-trip, and so users recognize a myMeal key.
TOKEN_PREFIX = "mm_"

# What a key may be used for:
#   full — REST API + MCP (default; the legacy all-access key)
#   rest — REST API only (rejected at the MCP server)
#   mcp  — MCP server only (rejected at the REST API)
TOKEN_SCOPES = ("full", "rest", "mcp")

# What a key may DO wherever its scope lets it work (orthogonal to scope):
#   write — read + mutate (default; every pre-existing key)
#   read  — read-only: REST rejects non-GET/HEAD, MCP rejects mutating tools
TOKEN_ACCESS = ("write", "read")


def generate_raw_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ApiToken(IDMixin, TimestampMixin, db.Model):
    __tablename__ = "api_tokens"

    name: Mapped[str] = mapped_column(String(255), default="")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # First few chars of the raw token, kept for display ("mm_ab12…").
    hint: Mapped[str] = mapped_column(String(16), default="")
    # full | rest | mcp — see TOKEN_SCOPES. server_default so keys created before
    # this column existed keep full (REST + MCP) access.
    scope: Mapped[str] = mapped_column(
        String(16), nullable=False, default="full", server_default="full"
    )
    # write | read — see TOKEN_ACCESS. server_default so keys created before this
    # column existed keep full mutate access.
    access: Mapped[str] = mapped_column(
        String(8), nullable=False, default="write", server_default="write"
    )
    last_used_at: Mapped[str] = mapped_column(DateTime, nullable=True)

    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    group_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("groups.id"), index=True
    )
    user = relationship("User")
