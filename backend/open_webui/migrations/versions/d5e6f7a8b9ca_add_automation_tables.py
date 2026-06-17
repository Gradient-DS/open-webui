"""add automation tables.

Revision ID: d5e6f7a8b9ca
Revises: a3dd5bedd151
Create Date: 2026-03-30

Original upstream revision id: d4e5f6a7b8c9
Renamed in Gradient-DS fork to d5e6f7a8b9ca due to a collision with our
add_soft_delete_columns migration (also d4e5f6a7b8c9). See plan:
thoughts/shared/plans/2026-05-24-open-webui-upstream-v0.9.5-merge.md

This rename also requires updating the child migration's down_revision:
backend/open_webui/migrations/versions/b7c8d9e0f1a2_add_last_read_at_to_chat.py
must point its down_revision at 'd5e6f7a8b9ca' (was 'd4e5f6a7b8c9').
"""

from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = 'd5e6f7a8b9ca'
down_revision: Union[str, None] = 'a3dd5bedd151'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'automation',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('data', sa.JSON(), nullable=False),
        sa.Column('meta', sa.JSON(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, default=True),
        sa.Column('last_run_at', sa.BigInteger(), nullable=True),
        sa.Column('next_run_at', sa.BigInteger(), nullable=True),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
    )
    op.create_index('ix_automation_next_run', 'automation', ['next_run_at'])

    op.create_table(
        'automation_run',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('automation_id', sa.Text(), nullable=False),
        sa.Column('chat_id', sa.Text(), nullable=True),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
    )
    op.create_index(
        'ix_automation_run_automation_id',
        'automation_run',
        ['automation_id'],
    )


def downgrade():
    op.drop_index('ix_automation_run_automation_id')
    op.drop_table('automation_run')
    op.drop_index('ix_automation_next_run')
    op.drop_table('automation')
