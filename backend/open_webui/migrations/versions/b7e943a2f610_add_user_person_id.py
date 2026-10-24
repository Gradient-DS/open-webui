"""Store the person id returned by soev-api for the user's login.

Revision ID: b7e943a2f610
Revises: 901d1275cce6
"""

from alembic import op
import sqlalchemy as sa

revision = 'b7e943a2f610'
down_revision = '901d1275cce6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('user', sa.Column('person_id', sa.String(), nullable=True))
    op.create_index('user_person_id_idx', 'user', ['person_id'], unique=True)


def downgrade() -> None:
    op.drop_index('user_person_id_idx', table_name='user')
    op.drop_column('user', 'person_id')
