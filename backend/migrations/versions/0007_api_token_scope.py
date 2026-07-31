"""add api_tokens.scope (full | rest | mcp)

Scopes let a key be restricted to REST-only or MCP-only. server_default="full"
so every pre-existing key keeps full (REST + MCP) access. Idempotent: the 0001
baseline builds api_tokens via create_all on fresh DBs (already with the column),
so this delta only adds it to DBs stamped before the column existed.

Revision ID: 0007_api_token_scope
Revises: 0006_add_ai_suggestions
Create Date: 2026-07-31
"""
import sqlalchemy as sa
from alembic import op

revision = "0007_api_token_scope"
down_revision = "0006_add_ai_suggestions"
branch_labels = None
depends_on = None


def _has_column(insp, table, col):
    return table in insp.get_table_names() and any(
        c["name"] == col for c in insp.get_columns(table)
    )


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if not _has_column(insp, "api_tokens", "scope"):
        op.add_column(
            "api_tokens",
            sa.Column("scope", sa.String(16), nullable=False, server_default="full"),
        )


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if _has_column(insp, "api_tokens", "scope"):
        with op.batch_alter_table("api_tokens") as batch:
            batch.drop_column("scope")
