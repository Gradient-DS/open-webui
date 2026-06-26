"""Backfill file.meta status/error from file.data

Revision ID: d651b063d0d8
Revises: 785970dd32b7
Create Date: 2026-06-24

Task 1 (dual-write) ensures new file rows always store status/error in both
``file.data`` and ``file.meta``.  This migration backfills ``meta.status``
and ``meta.error`` for existing rows that were written before Task 1, so
Task 3's read path (which reads from ``meta``) is correct for all rows,
including already-synced KB files that cloud_hash deduplication will never
re-sync.

Cross-DB guarantee
------------------
Both ``file.data`` and ``file.meta`` are declared as ``JSON`` (not JSONB)
columns.  This migration uses **pure Python / SQLAlchemy Core only** — no
dialect-specific JSON SQL operators (``jsonb_set``, ``||``, ``?``,
``json_set``).  Rows are fetched in Python, merged in Python, and written
back via a plain ``UPDATE … WHERE id = :id`` with the JSON value bound as
``sa.JSON()``.  The migration therefore runs identically on SQLite and
PostgreSQL.

Batching
--------
Rows are processed in windowed batches of 200, ordered by ``id``.  Only rows
whose ``data`` dict contains a ``status`` key are touched; rows without
``data.status`` are intentionally left unchanged — this is a correctness
property, not just a performance optimisation (those rows were never synced
and have no status to backfill).  Each batch emits targeted ``UPDATE``
statements so only affected rows are written.  The windowed SELECT pattern
avoids loading the entire table into memory at once.
(Note: Alembic wraps the upgrade in a single DDL transaction; the batching
here is a memory / row-fetch guard, not per-batch commit isolation.  For the
file table sizes in production this is the right trade-off.)

Idempotency
-----------
If the migration is re-run, rows whose ``meta.status`` already equals
``data.status`` receive an identical UPDATE — safe to repeat.

Downgrade
---------
``downgrade()`` is a no-op.  Stripping ``meta.status``/``meta.error`` during
a downgrade would create an inconsistency with rows inserted by Task 1 (which
continues to dual-write).  Leaving the keys in place is the safer choice, and
the keys are additive (no schema change).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.sql import column, table

revision = 'd651b063d0d8'
down_revision = '785970dd32b7'
branch_labels = None
depends_on = None

_BATCH_SIZE = 200

# Lightweight table reference — only the columns we touch.
_file = table(
    'file',
    column('id', sa.String()),
    column('data', sa.JSON()),
    column('meta', sa.JSON()),
)


def _backfill(bind):
    """Backfill ``meta.status`` / ``meta.error`` from ``data`` for all rows.

    Extracted from ``upgrade()`` so tests can import and call this function
    directly against a plain SQLAlchemy connection, exercising the real
    ``sa.select`` / ``sa.update`` path rather than a reimplementation.

    ``bind`` must be an open SQLAlchemy ``Connection``.
    """
    offset = 0

    while True:
        # Fetch the next window of rows, ordered by PK so pagination is stable.
        rows = bind.execute(
            sa.select(_file.c.id, _file.c.data, _file.c.meta).order_by(_file.c.id).limit(_BATCH_SIZE).offset(offset)
        ).fetchall()

        if not rows:
            break

        for row in rows:
            data = row.data
            # Skip rows where data is not a dict or has no 'status' key
            # (e.g. plain chat uploads that never had a sync status).
            if not isinstance(data, dict) or 'status' not in data:
                continue

            meta = row.meta if isinstance(row.meta, dict) else {}

            # Build updated meta: preserve ALL existing meta keys, then overlay
            # status (and error if present in data).  Never touch data.
            new_meta = dict(meta)
            new_meta['status'] = data['status']
            if 'error' in data:
                new_meta['error'] = data['error']

            bind.execute(sa.update(_file).where(_file.c.id == row.id).values(meta=new_meta))

        offset += _BATCH_SIZE

        if len(rows) < _BATCH_SIZE:
            break


def upgrade():
    _backfill(op.get_bind())


def downgrade():
    # No-op: see module docstring for rationale.
    pass
