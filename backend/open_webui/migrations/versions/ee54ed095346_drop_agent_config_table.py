"""drop agent_config table

Revision ID: ee54ed095346
Revises: e24b7c9d1f63
Create Date: 2026-10-12

[Gradient] Removes the v1 agent picker's ``agent_config`` table and its
``access_grant`` rows. A rollback to v1 redeploys the previous image set
and restores the database backup; downgrade recreates the empty table.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'ee54ed095346'
down_revision: Union[str, None] = 'e24b7c9d1f63'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    tables = _tables()
    if 'access_grant' in tables:
        op.execute(sa.text("DELETE FROM access_grant WHERE resource_type = 'agent_config'"))
    if 'agent_config' in tables:
        op.drop_table('agent_config')


def downgrade() -> None:
    if 'agent_config' in _tables():
        return
    op.create_table(
        'agent_config',
        sa.Column('id', sa.Text(), nullable=False, primary_key=True),
        sa.Column('user_id', sa.Text(), nullable=True),
        sa.Column('name', sa.Text(), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('profile_image_url', sa.Text(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('is_beta', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('meta', sa.JSON(), nullable=True, server_default='{}'),
        sa.Column('created_at', sa.BigInteger(), nullable=True),
        sa.Column('updated_at', sa.BigInteger(), nullable=True),
        sa.Column('position', sa.Integer(), nullable=False, server_default=sa.text('0')),
    )
