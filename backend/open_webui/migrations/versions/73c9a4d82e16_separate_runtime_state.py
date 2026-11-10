"""Move worker cursors and offboarding receipts out of configuration."""

import sqlalchemy as sa
from alembic import op

revision = '73c9a4d82e16'
down_revision = '6d9a2c4e8b10'
branch_labels = None
depends_on = None


def upgrade() -> None:
    state = op.create_table(
        'runtime_state',
        sa.Column('key', sa.Text(), primary_key=True),
        sa.Column('value', sa.JSON(), nullable=False),
    )
    config = sa.table('config', sa.column('key', sa.Text()), sa.column('value', sa.JSON()))
    workers = sa.or_(config.c.key.startswith('soev.retention.'), config.c.key.startswith('soev.offboarding.'))
    op.execute(state.insert().from_select(['key', 'value'], sa.select(config.c.key, config.c.value).where(workers)))
    op.execute(config.delete().where(workers))


def downgrade() -> None:
    state = sa.table('runtime_state', sa.column('key', sa.Text()), sa.column('value', sa.JSON()))
    config = sa.table('config', sa.column('key', sa.Text()), sa.column('value', sa.JSON()))
    op.execute(config.insert().from_select(['key', 'value'], sa.select(state.c.key, state.c.value)))
    op.drop_table('runtime_state')
