"""Record successful directory snapshot evidence atomically with memberships."""

import sqlalchemy as sa
from alembic import op

revision = 'da27c90f4b61'
down_revision = 'a8d4e6b92c10'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'soev_group_snapshot',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('fetched_at', sa.Float(), nullable=False),
        sa.Column('completed_at', sa.Float(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('soev_group_snapshot')
