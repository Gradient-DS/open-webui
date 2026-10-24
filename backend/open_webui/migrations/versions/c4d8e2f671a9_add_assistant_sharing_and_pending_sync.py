"""Record derived assistant grants and owner-request sync work."""

import sqlalchemy as sa
from alembic import op

revision = 'c4d8e2f671a9'
down_revision = 'b7e943a2f610'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'assistant_share_grant',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('owner_id', sa.Text(), nullable=False),
        sa.Column('assistant_id', sa.Text(), nullable=False),
        sa.Column('resource_type', sa.Text(), nullable=False),
        sa.Column('resource_id', sa.Text(), nullable=False),
        sa.Column('principal_type', sa.Text(), nullable=False),
        sa.Column('principal_id', sa.Text(), nullable=False),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.UniqueConstraint(
            'assistant_id',
            'resource_type',
            'resource_id',
            'principal_type',
            'principal_id',
            name='uq_assistant_share_grant',
        ),
    )
    op.create_index('ix_assistant_share_grant_owner_id', 'assistant_share_grant', ['owner_id'])
    op.create_index('ix_assistant_share_grant_assistant_id', 'assistant_share_grant', ['assistant_id'])
    op.create_table(
        'soev_pending_sync',
        sa.Column('owner_id', sa.Text(), primary_key=True),
        sa.Column('kind', sa.Text(), primary_key=True),
        sa.Column('resource_id', sa.Text(), primary_key=True),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.CheckConstraint("kind IN ('folder_files', 'assistant_files')", name='ck_soev_pending_sync_kind'),
    )


def downgrade() -> None:
    op.drop_table('soev_pending_sync')
    op.drop_table('assistant_share_grant')
