"""add api_tokens.access (write | read)

An access class orthogonal to scope: a `read` key is read-only wherever it works
(REST rejects non-GET/HEAD; MCP rejects mutating tools). server_default="write"
so every pre-existing key keeps full mutate access. Idempotent: fresh DBs build
api_tokens via create_all already with the column, so this delta only adds it to
DBs stamped before the column existed.

Revision ID: 0008_api_token_access
Revises: 0007_api_token_scope
Create Date: 2026-07-31
"""
import sqlalchemy as sa
from alembic import op

revision = "0008_api_token_access"
down_revision = "0007_api_token_scope"
branch_labels = None
depends_on = None


def _has_column(insp, table, col):
    return table in insp.get_table_names() and any(
        c["name"] == col for c in insp.get_columns(table)
    )


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if not _has_column(insp, "api_tokens", "access"):
        op.add_column(
            "api_tokens",
            sa.Column("access", sa.String(8), nullable=False, server_default="write"),
        )


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if _has_column(insp, "api_tokens", "access"):
        with op.batch_alter_table("api_tokens") as batch:
            batch.drop_column("access")
