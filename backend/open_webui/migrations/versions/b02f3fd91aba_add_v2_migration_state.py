"""[Gradient] Add the v2 migration state tables: config snapshot, model id backup, step markers.

Revision ID: b02f3fd91aba
Revises: e24b7c9d1f63
Create Date: 2026-10-05

"""

import sqlalchemy as sa
from alembic import op

revision = 'b02f3fd91aba'
down_revision = 'e24b7c9d1f63'
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if 'config_backup' not in tables:
        op.create_table(
            'config_backup',
            sa.Column('migration_id', sa.Text(), primary_key=True),
            sa.Column('key', sa.Text(), primary_key=True),
            sa.Column('value', sa.Text(), nullable=True),
            sa.Column('updated_at', sa.BigInteger(), nullable=True),
            sa.Column('created_at', sa.BigInteger(), nullable=False),
        )
    if 'model_id_backup' not in tables:
        op.create_table(
            'model_id_backup',
            sa.Column('migration_id', sa.Text(), primary_key=True),
            sa.Column('site', sa.Text(), primary_key=True),
            sa.Column('row_id', sa.Text(), primary_key=True),
            sa.Column('path', sa.Text(), primary_key=True),
            sa.Column('old_value', sa.Text(), nullable=False),
            sa.Column('new_value', sa.Text(), nullable=False),
            sa.Column('created_at', sa.BigInteger(), nullable=False),
        )
    if 'migration_marker' not in tables:
        op.create_table(
            'migration_marker',
            sa.Column('migration_id', sa.Text(), primary_key=True),
            sa.Column('step', sa.Text(), primary_key=True),
            sa.Column('created_at', sa.BigInteger(), nullable=False),
        )


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    for table in ('migration_marker', 'model_id_backup', 'config_backup'):
        if table in tables:
            op.drop_table(table)
