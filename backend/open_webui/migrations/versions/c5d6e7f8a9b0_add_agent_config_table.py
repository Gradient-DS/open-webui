"""add agent_config table

Revision ID: c5d6e7f8a9b0
Revises: a1b2c3d4e5f8
Create Date: 2026-04-26

[Gradient] Adds agent_config table — admin-managed metadata for external
agents (display name, description, CTA copy, profile image, active flag,
beta flag). Access control is stored in the existing access_grant table
under resource_type='agent_config'.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from open_webui.migrations.util import get_existing_tables

revision: str = 'c5d6e7f8a9b0'
down_revision: Union[str, None] = 'a1b2c3d4e5f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    existing_tables = set(get_existing_tables())
    if 'agent_config' not in existing_tables:
        op.create_table(
            'agent_config',
            sa.Column('id', sa.Text(), nullable=False, primary_key=True),
            sa.Column('user_id', sa.Text(), nullable=True),
            sa.Column('name', sa.Text(), nullable=True),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('profile_image_url', sa.Text(), nullable=True),
            sa.Column('cta_copy', sa.Text(), nullable=True),
            sa.Column(
                'is_active',
                sa.Boolean(),
                nullable=False,
                server_default=sa.text('false'),
            ),
            sa.Column(
                'is_beta',
                sa.Boolean(),
                nullable=False,
                server_default=sa.text('true'),
            ),
            sa.Column('meta', sa.JSON(), nullable=True, server_default='{}'),
            sa.Column('created_at', sa.BigInteger(), nullable=True),
            sa.Column('updated_at', sa.BigInteger(), nullable=True),
        )


def downgrade() -> None:
    existing_tables = set(get_existing_tables())
    if 'agent_config' in existing_tables:
        op.drop_table('agent_config')
