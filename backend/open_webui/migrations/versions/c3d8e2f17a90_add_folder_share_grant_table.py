"""[Gradient] add folder_share_grant: the read grants a folder share added to its owner's knowledge and notes

Revision ID: c3d8e2f17a90
Revises: b7e3c1d94a25
Create Date: 2026-10-17 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c3d8e2f17a90'
down_revision: str | None = 'b7e3c1d94a25'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'folder_share_grant',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('owner_id', sa.Text(), nullable=False),
        sa.Column('resource_type', sa.Text(), nullable=False),
        sa.Column('resource_id', sa.Text(), nullable=False),
        sa.Column('principal_type', sa.Text(), nullable=False),
        sa.Column('principal_id', sa.Text(), nullable=False),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.UniqueConstraint(
            'resource_type', 'resource_id', 'principal_type', 'principal_id', name='uq_folder_share_grant'
        ),
    )
    op.create_index('ix_folder_share_grant_owner_id', 'folder_share_grant', ['owner_id'])


def downgrade() -> None:
    op.drop_index('ix_folder_share_grant_owner_id', table_name='folder_share_grant')
    op.drop_table('folder_share_grant')
