"""add data warning log table

Revision ID: a1b2c3d4e5f8
Revises: c3d4e5f6a7b8
Create Date: 2026-04-09
"""

from alembic import op
import sqlalchemy as sa
from open_webui.migrations.util import get_existing_tables

revision = 'a1b2c3d4e5f8'
down_revision = 'c3d4e5f6a7b8'
branch_labels = None
depends_on = None


def upgrade():
    existing_tables = set(get_existing_tables())
    if 'data_warning_log' not in existing_tables:
        op.create_table(
            'data_warning_log',
            sa.Column('id', sa.String(), primary_key=True),
            sa.Column('user_id', sa.String(), nullable=False, index=True),
            sa.Column('chat_id', sa.String(), nullable=False, index=True),
            sa.Column('model_id', sa.String(), nullable=False),
            sa.Column('capabilities', sa.JSON(), nullable=False),
            sa.Column('warning_message', sa.Text(), nullable=True),
            sa.Column('created_at', sa.BigInteger(), nullable=False),
        )


def downgrade():
    op.drop_table('data_warning_log')
