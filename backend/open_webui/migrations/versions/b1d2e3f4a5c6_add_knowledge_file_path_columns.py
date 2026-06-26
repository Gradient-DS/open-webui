"""Add denormalized path columns + composite index to knowledge_file

Revision ID: b1d2e3f4a5c6
Revises: e610a1faaa3e
Create Date: 2026-06-25

Schema half of the lazy per-folder KB tree browser (Tier 2). Adds two nullable
columns to the ``knowledge_file`` join table — ``relative_path`` and
``source_item_id`` — mirrored from ``file.meta`` by the write path, plus the
composite index that makes a single-folder listing an index range scan instead
of a full-KB JSON re-parse:

    ix_kf_kb_source_relpath  ON knowledge_file (knowledge_id, source_item_id, relative_path)

On PostgreSQL ``relative_path`` uses the ``text_pattern_ops`` opclass so a
``WHERE knowledge_id=? AND source_item_id=? AND relative_path LIKE 'P/%'``
prefix scan is index-driven regardless of the database collation. The
``postgresql_ops`` kwarg is silently ignored on SQLite, where a plain composite
index still serves the same prefix LIKE (text_pattern_ops is a no-op there).

The companion data backfill lives in revision ``a1c2e3f4d5b6``; this migration
is schema-only so it stays fast and transactional.

Idempotent: column/index creation is guarded so a re-run of the migrate-job is
a no-op.
"""

from alembic import op
import sqlalchemy as sa

revision = 'b1d2e3f4a5c6'
down_revision = 'e610a1faaa3e'
branch_labels = None
depends_on = None

_INDEX_NAME = 'ix_kf_kb_source_relpath'


def _column_exists(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return column_name in [c['name'] for c in inspector.get_columns(table_name)]


def _index_exists(table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return index_name in [ix['name'] for ix in inspector.get_indexes(table_name)]


def upgrade():
    if not _column_exists('knowledge_file', 'relative_path'):
        op.add_column('knowledge_file', sa.Column('relative_path', sa.Text(), nullable=True))
    if not _column_exists('knowledge_file', 'source_item_id'):
        op.add_column('knowledge_file', sa.Column('source_item_id', sa.Text(), nullable=True))

    if not _index_exists('knowledge_file', _INDEX_NAME):
        op.create_index(
            _INDEX_NAME,
            'knowledge_file',
            ['knowledge_id', 'source_item_id', 'relative_path'],
            postgresql_ops={'relative_path': 'text_pattern_ops'},
        )


def downgrade():
    if _index_exists('knowledge_file', _INDEX_NAME):
        op.drop_index(_INDEX_NAME, table_name='knowledge_file')
    if _column_exists('knowledge_file', 'source_item_id'):
        op.drop_column('knowledge_file', 'source_item_id')
    if _column_exists('knowledge_file', 'relative_path'):
        op.drop_column('knowledge_file', 'relative_path')
