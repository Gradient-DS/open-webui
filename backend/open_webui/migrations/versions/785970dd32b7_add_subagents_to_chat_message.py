"""add subagents column to chat_message

Revision ID: 785970dd32b7
Revises: 959ca43c7bb2
Create Date: 2026-05-29

Adds the per-message ``subagents`` JSON column so SubAgent lifecycle
events (live-streamed during a turn) survive page reload. Without
this column, the fast-path read in ``get_messages_map_by_chat_id``
returns the ``chat_message`` row alone — and the row had no place
to keep the ``subagents`` array.

Mirrors the existing ``status_history`` column pattern: nullable
JSON, no default. The frontend reducer (``reduceSubAgents``) treats
``null`` / ``undefined`` / ``[]`` identically, so old rows render
no cards (their original behaviour).
"""

from alembic import op
import sqlalchemy as sa


revision = '785970dd32b7'
down_revision = '959ca43c7bb2'
branch_labels = None
depends_on = None


def _column_exists(table_name: str, column_name: str) -> bool:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c['name'] for c in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade():
    if _column_exists('chat_message', 'subagents'):
        return
    op.add_column('chat_message', sa.Column('subagents', sa.JSON(), nullable=True))


def downgrade():
    if _column_exists('chat_message', 'subagents'):
        op.drop_column('chat_message', 'subagents')
