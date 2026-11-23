"""[Gradient] Assistant-owned skill folders."""

import sqlalchemy as sa
from alembic import op

revision = 'a8d42e7c913b'
down_revision = 'f3a9c1d27b40'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'assistant_skill_file',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('assistant_id', sa.Text(), sa.ForeignKey('model.id', ondelete='CASCADE'), nullable=False),
        sa.Column('file_id', sa.String(), sa.ForeignKey('file.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('path', sa.String(255), nullable=False),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
        sa.UniqueConstraint('assistant_id', 'path', name='uq_assistant_skill_file_path'),
    )


def downgrade() -> None:
    op.drop_table('assistant_skill_file')
