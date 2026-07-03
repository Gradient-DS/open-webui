"""add last_read_at to chat

Revision ID: b7c8d9e0f1a2
Revises: d5e6f7a8b9ca
Create Date: 2026-04-01 04:00:00.000000

Note: down_revision was 'd4e5f6a7b8c9' upstream (add_automation_tables),
but we renamed that migration to 'd5e6f7a8b9ca' in the Gradient-DS fork
to avoid a collision with our own d4e5f6a7b8c9 (add_soft_delete_columns).
See thoughts/shared/plans/2026-05-24-open-webui-upstream-v0.9.5-merge.md.
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b7c8d9e0f1a2'
down_revision = 'd5e6f7a8b9ca'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('chat', sa.Column('last_read_at', sa.BigInteger(), nullable=True))
    # Set existing chats to be marked as read
    op.execute('UPDATE chat SET last_read_at = updated_at')


def downgrade():
    op.drop_column('chat', 'last_read_at')
