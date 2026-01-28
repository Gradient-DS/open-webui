"""Add user_archive table

Revision ID: f8e1a9c2d3b4
Revises: 018012973d35, c440947495f3
Create Date: 2026-01-28

"""

from alembic import op
import sqlalchemy as sa

revision = "f8e1a9c2d3b4"
down_revision = ("018012973d35", "c440947495f3")
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_archive",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("user_email", sa.Text(), nullable=False),
        sa.Column("user_name", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("archived_by", sa.Text(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("retention_days", sa.BigInteger(), nullable=True),
        sa.Column("expires_at", sa.BigInteger(), nullable=True),
        sa.Column("never_delete", sa.Boolean(), default=False),
        sa.Column("restored", sa.Boolean(), default=False),
        sa.Column("restored_at", sa.BigInteger(), nullable=True),
        sa.Column("restored_by", sa.Text(), nullable=True),
        sa.Column("restored_user_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
    )

    op.create_index("user_archive_user_email_idx", "user_archive", ["user_email"])
    op.create_index("user_archive_user_name_idx", "user_archive", ["user_name"])
    op.create_index("user_archive_expires_at_idx", "user_archive", ["expires_at"])
    op.create_index("user_archive_created_at_idx", "user_archive", ["created_at"])


def downgrade():
    op.drop_index("user_archive_created_at_idx", table_name="user_archive")
    op.drop_index("user_archive_expires_at_idx", table_name="user_archive")
    op.drop_index("user_archive_user_name_idx", table_name="user_archive")
    op.drop_index("user_archive_user_email_idx", table_name="user_archive")
    op.drop_table("user_archive")
