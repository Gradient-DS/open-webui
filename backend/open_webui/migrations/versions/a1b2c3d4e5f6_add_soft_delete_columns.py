"""add soft delete columns

Revision ID: a1b2c3d4e5f6
Revises: eaa33ce2752e
Create Date: 2026-02-19
"""

from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f6"
down_revision = "eaa33ce2752e"
branch_labels = None
depends_on = None


def upgrade():
    # Knowledge table
    op.add_column("knowledge", sa.Column("deleted_at", sa.BigInteger(), nullable=True))
    op.create_index("ix_knowledge_deleted_at", "knowledge", ["deleted_at"])

    # Chat table
    op.add_column("chat", sa.Column("deleted_at", sa.BigInteger(), nullable=True))
    op.create_index("ix_chat_deleted_at", "chat", ["deleted_at"])


def downgrade():
    op.drop_index("ix_chat_deleted_at", table_name="chat")
    op.drop_column("chat", "deleted_at")

    op.drop_index("ix_knowledge_deleted_at", table_name="knowledge")
    op.drop_column("knowledge", "deleted_at")
