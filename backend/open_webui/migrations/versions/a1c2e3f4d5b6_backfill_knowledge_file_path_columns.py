"""Backfill knowledge_file.relative_path / source_item_id from file.meta

Revision ID: a1c2e3f4d5b6
Revises: b1d2e3f4a5c6
Create Date: 2026-06-25

Revision ``b1d2e3f4a5c6`` added the two denormalized path columns to
``knowledge_file`` (NULL for every existing row). This migration copies
``relative_path`` and ``source_item_id`` out of the joined ``file.meta`` for
each existing link so the lazy per-folder tree browser can range-scan folders
for KBs that were synced before the write-path maintenance landed.

Cross-DB guarantee
------------------
``file.meta`` is a ``JSON`` (not JSONB) column. This migration uses **pure
Python / SQLAlchemy Core only** — no dialect-specific JSON operators. Rows are
joined and read in Python, the path fields extracted in Python, and written
back with a plain ``UPDATE … WHERE id = :id``. It therefore runs identically on
SQLite and PostgreSQL.

Cheapness
---------
``file.meta`` is a small column (no de-TOAST of the large ``file.data.content``
blob), so the join read is cheap. Rows are still processed in windowed batches
of 500 ordered by ``knowledge_file.id`` to bound memory / row-fetch.

Idempotency
-----------
Re-running writes identical values. Only rows whose ``meta`` actually carries a
``relative_path`` or ``source_item_id`` are touched; loose-file links (local
uploads with no provider path) are intentionally left at their NULL default —
this matches what the write path stores for those rows.

Downgrade
---------
No-op. The columns themselves are dropped by ``b1d2e3f4a5c6``'s downgrade; there
is nothing data-wise to undo here.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.sql import column, table

revision = 'a1c2e3f4d5b6'
down_revision = 'b1d2e3f4a5c6'
branch_labels = None
depends_on = None

_BATCH_SIZE = 500

# Lightweight table references — only the columns we touch / read.
_kf = table(
    'knowledge_file',
    column('id', sa.String()),
    column('file_id', sa.String()),
    column('relative_path', sa.Text()),
    column('source_item_id', sa.Text()),
)
_file = table(
    'file',
    column('id', sa.String()),
    column('meta', sa.JSON()),
)


def _path_fields_from_meta(meta):
    """Inlined copy of models.knowledge._path_fields_from_meta.

    Migrations must stay self-contained — they cannot import app code that may
    change shape underneath an old revision. Keep this in sync with the source.
    """
    if not isinstance(meta, dict):
        return None, None
    relative_path = meta.get('relative_path')
    source_item_id = meta.get('source_item_id')
    return (
        relative_path if isinstance(relative_path, str) else None,
        source_item_id if isinstance(source_item_id, str) else None,
    )


def _backfill(bind):
    """Copy path fields from ``file.meta`` into ``knowledge_file`` for all links.

    Extracted from ``upgrade()`` so tests can import and call it directly
    against a plain SQLAlchemy ``Connection``.
    """
    offset = 0

    while True:
        rows = bind.execute(
            sa.select(_kf.c.id, _file.c.meta)
            .select_from(_kf.join(_file, _kf.c.file_id == _file.c.id))
            .order_by(_kf.c.id)
            .limit(_BATCH_SIZE)
            .offset(offset)
        ).fetchall()

        if not rows:
            break

        for row in rows:
            relative_path, source_item_id = _path_fields_from_meta(row.meta)
            # Leave loose-file links at their NULL default; only write rows that
            # actually carry a provider path.
            if relative_path is None and source_item_id is None:
                continue
            bind.execute(
                sa.update(_kf)
                .where(_kf.c.id == row.id)
                .values(relative_path=relative_path, source_item_id=source_item_id)
            )

        offset += _BATCH_SIZE

        if len(rows) < _BATCH_SIZE:
            break


def upgrade():
    _backfill(op.get_bind())


def downgrade():
    # No-op: see module docstring for rationale.
    pass
