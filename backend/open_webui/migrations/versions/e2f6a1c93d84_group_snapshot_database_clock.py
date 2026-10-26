"""Use database-clock evidence for the directory mirror."""

import sqlalchemy as sa
from alembic import op

revision = 'e2f6a1c93d84'
down_revision = 'c7e4a9b26d10'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Old timestamps came from worker clocks and cannot establish validity.
    with op.batch_alter_table('soev_group_snapshot') as batch:
        batch.add_column(sa.Column('fetch_started_at', sa.Float(), nullable=True))
        batch.add_column(sa.Column('pull_started_at', sa.Float(), nullable=True))
        batch.drop_column('generation')
        batch.drop_column('pull_seconds')
    op.execute('UPDATE soev_group_snapshot SET lease_token = NULL, lease_until = 0, next_pull_at = 0')


def downgrade() -> None:
    with op.batch_alter_table('soev_group_snapshot') as batch:
        batch.add_column(sa.Column('generation', sa.BigInteger(), nullable=False, server_default='0'))
        batch.add_column(sa.Column('pull_seconds', sa.Float(), nullable=False, server_default='0'))
        batch.drop_column('pull_started_at')
        batch.drop_column('fetch_started_at')
