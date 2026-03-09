"""add knowledge type column

Revision ID: 2c5f92a9fd66
Revises: f8e1a9c2d3b4
Create Date: 2026-02-04

"""

from alembic import op
import sqlalchemy as sa
from open_webui.migrations.util import get_existing_tables

revision = "2c5f92a9fd66"
down_revision = "f8e1a9c2d3b4"
branch_labels = None
depends_on = None


def upgrade():
    existing_tables = get_existing_tables()
    if "knowledge" in existing_tables:
        op.add_column(
            "knowledge",
            sa.Column("type", sa.Text(), nullable=False, server_default="local"),
        )

        # Data migration: set type="onedrive" for KBs with onedrive_sync in meta
        conn = op.get_bind()
        dialect = conn.dialect.name

        if dialect == "sqlite":
            conn.execute(
                sa.text(
                    """
                    UPDATE knowledge
                    SET type = 'onedrive'
                    WHERE json_extract(meta, '$.onedrive_sync') IS NOT NULL
                    """
                )
            )
        else:
            # PostgreSQL
            conn.execute(
                sa.text(
                    """
                    UPDATE knowledge
                    SET type = 'onedrive'
                    WHERE meta::jsonb ? 'onedrive_sync'
                    """
                )
            )


def downgrade():
    op.drop_column("knowledge", "type")
