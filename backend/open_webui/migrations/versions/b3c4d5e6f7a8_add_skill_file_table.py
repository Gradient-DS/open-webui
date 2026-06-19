"""Add skill_file table

Revision ID: b3c4d5e6f7a8
Revises: 785970dd32b7
Create Date: 2026-06-19 15:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from open_webui.migrations.util import get_existing_tables

# revision identifiers, used by Alembic.
revision: str = 'b3c4d5e6f7a8'
down_revision: str | None = '785970dd32b7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    existing_tables = set(get_existing_tables())

    if 'skill_file' not in existing_tables:
        op.create_table(
            'skill_file',
            sa.Column('id', sa.String(), primary_key=True),
            sa.Column('user_id', sa.String(), nullable=False),
            sa.Column(
                'skill_id',
                sa.String(),
                sa.ForeignKey('skill.id', ondelete='CASCADE'),
                nullable=False,
            ),
            sa.Column(
                'file_id',
                sa.String(),
                sa.ForeignKey('file.id', ondelete='CASCADE'),
                nullable=False,
            ),
            sa.Column('created_at', sa.BigInteger(), nullable=False),
            sa.Column('updated_at', sa.BigInteger(), nullable=False),
            # indexes
            sa.Index('ix_skill_file_skill_id', 'skill_id'),
            sa.Index('ix_skill_file_file_id', 'file_id'),
            sa.Index('ix_skill_file_user_id', 'user_id'),
            # unique constraint — prevent duplicate (skill, file) pairs
            sa.UniqueConstraint('skill_id', 'file_id', name='uq_skill_file_skill_file'),
        )


def downgrade() -> None:
    op.drop_table('skill_file')
