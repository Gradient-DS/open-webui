"""add position column to agent_config

Revision ID: e7f8a9b0c1d2
Revises: d6e7f8a9b0c1
Create Date: 2026-04-30

[Gradient] Adds an integer ``position`` column to ``agent_config`` so
admins can drag-and-drop reorder agents in the admin panel and the
chosen order propagates to the user-facing picker. Existing rows are
back-filled with positions assigned in alphabetical name order so the
upgrade is non-disruptive — the visible ordering on first boot matches
the previous implicit order.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from open_webui.migrations.util import get_existing_tables

revision: str = 'e7f8a9b0c1d2'
down_revision: Union[str, None] = 'd6e7f8a9b0c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table_name: str, column_name: str) -> bool:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    return column_name in {c['name'] for c in inspector.get_columns(table_name)}


def upgrade() -> None:
    if 'agent_config' not in set(get_existing_tables()):
        return
    if _column_exists('agent_config', 'position'):
        return

    op.add_column(
        'agent_config',
        sa.Column(
            'position',
            sa.Integer(),
            nullable=False,
            server_default=sa.text('0'),
        ),
    )

    conn = op.get_bind()
    rows = conn.execute(sa.text('SELECT id FROM agent_config ORDER BY name ASC, id ASC')).fetchall()
    for index, row in enumerate(rows):
        conn.execute(
            sa.text('UPDATE agent_config SET position = :pos WHERE id = :id'),
            {'pos': index, 'id': row[0]},
        )


def downgrade() -> None:
    if 'agent_config' not in set(get_existing_tables()):
        return
    if not _column_exists('agent_config', 'position'):
        return
    op.drop_column('agent_config', 'position')
