"""[Gradient] allow two users to hold a skill with the same name

Skills are private by default, so one user's skill name must not block another user's upload of the same bundle.

Revision ID: f3a9c1d27b40
Revises: 901d1275cce6
Create Date: 2026-11-20 09:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f3a9c1d27b40'
down_revision: Union[str, None] = '901d1275cce6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# SQLite leaves the column's unique constraint unnamed; the convention gives the batch copy a name to drop.
NAMING = {'uq': 'uq_%(table_name)s_%(column_0_name)s'}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'skill' not in inspector.get_table_names():
        return
    if bind.dialect.name == 'sqlite':
        with op.batch_alter_table('skill', naming_convention=NAMING) as batch:
            batch.drop_constraint('uq_skill_name', type_='unique')
        return
    for constraint in inspector.get_unique_constraints('skill'):
        if constraint['column_names'] == ['name']:
            op.drop_constraint(constraint['name'], 'skill', type_='unique')
    for index in inspector.get_indexes('skill'):
        if index.get('unique') and index['column_names'] == ['name']:
            op.drop_index(index['name'], table_name='skill')


def downgrade() -> None:
    with op.batch_alter_table('skill', naming_convention=NAMING) as batch:
        batch.create_unique_constraint('uq_skill_name', ['name'])
