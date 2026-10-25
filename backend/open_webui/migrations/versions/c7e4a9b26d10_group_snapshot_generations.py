"""Coordinate directory pulls and expose committed snapshot generations."""

import sqlalchemy as sa
from alembic import op

revision = 'c7e4a9b26d10'
down_revision = 'b86d2f09a137'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('soev_group_snapshot', sa.Column('generation', sa.BigInteger(), nullable=False, server_default='0'))
    op.add_column('soev_group_snapshot', sa.Column('pull_seconds', sa.Float(), nullable=False, server_default='0'))
    op.add_column('soev_group_snapshot', sa.Column('lease_token', sa.Text(), nullable=True))
    op.add_column('soev_group_snapshot', sa.Column('lease_until', sa.Float(), nullable=False, server_default='0'))
    op.add_column('soev_group_snapshot', sa.Column('next_pull_at', sa.Float(), nullable=False, server_default='0'))
    op.execute(
        'INSERT INTO soev_group_snapshot (id, fetched_at, completed_at) '
        "SELECT 'directory', 0, 0 WHERE NOT EXISTS (SELECT 1 FROM soev_group_snapshot WHERE id = 'directory')"
    )


def downgrade() -> None:
    with op.batch_alter_table('soev_group_snapshot') as batch:
        for name in ('next_pull_at', 'lease_until', 'lease_token', 'pull_seconds', 'generation'):
            batch.drop_column(name)
