"""Add the platform thread identity without deriving it from legacy content."""

import sqlalchemy as sa
from alembic import op

revision = 'a8d4e6b92c10'
down_revision = 'f73a9c0e2b61'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('chat', sa.Column('thread_id', sa.String(), nullable=True))
    op.create_table(
        'chat_thread',
        sa.Column('thread_id', sa.String(), primary_key=True),
        sa.Column('chat_id', sa.String(), nullable=False, index=True),
    )


def downgrade() -> None:
    op.drop_table('chat_thread')
    op.drop_column('chat', 'thread_id')
