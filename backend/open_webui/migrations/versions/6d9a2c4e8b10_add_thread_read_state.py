"""Store each reader's conversation watermark with fractional-second precision."""

import sqlalchemy as sa
from alembic import op

revision = '6d9a2c4e8b10'
down_revision = 'e2f6a1c93d84'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'thread_read',
        sa.Column('user_id', sa.String(), sa.ForeignKey('user.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('conversation_id', sa.String(), primary_key=True),
        sa.Column('last_read_at', sa.Float(precision=53), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('thread_read')
