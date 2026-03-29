"""add soft delete columns

Revision ID: d4e5f6a7b8c9
Revises: eaa33ce2752e
Create Date: 2026-02-19
"""

from alembic import op
import sqlalchemy as sa

revision = "d4e5f6a7b8c9"
down_revision = "eaa33ce2752e"
branch_labels = None
depends_on = None


def _column_exists(table_name: str, column_name: str) -> bool:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c["name"] for c in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade():
    # Knowledge table
    if not _column_exists("knowledge", "deleted_at"):
        op.add_column("knowledge", sa.Column("deleted_at", sa.BigInteger(), nullable=True))
        op.create_index("ix_knowledge_deleted_at", "knowledge", ["deleted_at"])

    # Chat table
    if not _column_exists("chat", "deleted_at"):
        op.add_column("chat", sa.Column("deleted_at", sa.BigInteger(), nullable=True))
        op.create_index("ix_chat_deleted_at", "chat", ["deleted_at"])


def downgrade():
    op.drop_index("ix_chat_deleted_at", table_name="chat")
    op.drop_column("chat", "deleted_at")

    op.drop_index("ix_knowledge_deleted_at", table_name="knowledge")
    op.drop_column("knowledge", "deleted_at")
