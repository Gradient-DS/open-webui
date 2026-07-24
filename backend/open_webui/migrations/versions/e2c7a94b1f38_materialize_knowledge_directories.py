"""Materialize knowledge_directory chains from knowledge_file path columns

Revision ID: e2c7a94b1f38
Revises: 2332928227f6
Create Date: 2026-07-23

P2-8 (KB frontend convergence) Phase 1 backfill: cloud-synced files carry
``relative_path`` / ``source_item_id`` on their ``knowledge_file`` rows but —
in the loader-worker era — never a ``directory_id``. This migration walks
every link row with ``relative_path IS NOT NULL AND directory_id IS NULL``
and materializes the corresponding ``knowledge_directory`` chain, mirroring
the write-path reverse bridge (``KnowledgeTable._materialize_directory_for_link``):

  * Folder-like sources become root directories named after the source's
    registry entry (``knowledge.meta[<provider>_sync].sources[].name``,
    fallback ``item_path`` basename, then the raw item id); the file's
    ``relative_path`` directories nest beneath it.
  * File-type sources get NO wrapper (their files stay at the KB root —
    PR #235 semantics); only sub-path directories (rare) are created.
  * Sources absent from the registry are skipped — the write path converges
    them after the registry self-heals.
  * The created root directory id is recorded as
    ``sources[].root_directory_id`` in the provider meta blob (consumed by
    remove-source cleanup and the source-mapped UI affordance).

Cross-DB guarantee
------------------
Pure Python / SQLAlchemy Core — no dialect-specific JSON operators. ``meta``
values are defensively ``json.loads``-ed when they arrive as strings, so the
migration behaves identically on SQLite and PostgreSQL.

Batching
--------
Link rows are walked per knowledge base in keyset batches of 500 ordered by
``knowledge_file.id``. Keyset (id > last) rather than OFFSET because each
batch UPDATE removes rows from the ``directory_id IS NULL`` filter — an
offset walk would silently skip half the rows.

Idempotency
-----------
Re-running is a no-op: backfilled rows no longer match ``directory_id IS
NULL``, and directory creation is find-or-create against the pre-loaded
``(parent_id, name)`` map. Loose/local links (no ``source_item_id``) are
never touched.

Downgrade
---------
No-op by design (plan: rollback drops nothing). The created directory rows
and ``directory_id`` values are invisible to the legacy tree UI, which reads
only the path columns.
"""

import json
import time
import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.sql import column, table

revision = 'e2c7a94b1f38'
down_revision = '2332928227f6'
branch_labels = None
depends_on = None

_BATCH_SIZE = 500

# Keep in sync with models.knowledge.SYNC_PROVIDER_META_KEYS (inlined —
# migrations must not import app code that may change under an old revision).
_SYNC_PROVIDER_META_KEYS = ('onedrive_sync', 'google_drive_sync', 'confluence_sync')

# Lightweight table references — only the columns we touch / read.
_knowledge = table(
    'knowledge',
    column('id', sa.Text()),
    column('user_id', sa.Text()),
    column('meta', sa.JSON()),
)
_directory = table(
    'knowledge_directory',
    column('id', sa.Text()),
    column('knowledge_id', sa.Text()),
    column('parent_id', sa.Text()),
    column('name', sa.Text()),
    column('user_id', sa.Text()),
    column('created_at', sa.BigInteger()),
    column('updated_at', sa.BigInteger()),
)
_kf = table(
    'knowledge_file',
    column('id', sa.Text()),
    column('knowledge_id', sa.Text()),
    column('user_id', sa.Text()),
    column('directory_id', sa.Text()),
    column('relative_path', sa.Text()),
    column('source_item_id', sa.Text()),
)


def _loads_meta(meta):
    """Normalize a ``meta`` cell to a dict (SQLite may hand back raw JSON text)."""
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except (TypeError, ValueError):
            return {}
    return meta if isinstance(meta, dict) else {}


def _source_map(meta):
    """``{item_id: (meta_key, entry)}`` from the provider sync blobs."""
    result = {}
    for meta_key in _SYNC_PROVIDER_META_KEYS:
        sync_info = meta.get(meta_key)
        if not isinstance(sync_info, dict):
            continue
        for entry in sync_info.get('sources') or []:
            if isinstance(entry, dict) and entry.get('item_id'):
                result.setdefault(entry['item_id'], (meta_key, entry))
    return result


def _segments_for(entry, relative_path, source_item_id):
    """Root→leaf directory names for one link (mirror of the write-path bridge).

    Returns ``(segments, wraps_source)`` — ``wraps_source`` is True when the
    first segment is the source-root wrapper (folder-like sources only).
    """
    path_dirs = [segment for segment in relative_path.split('/')[:-1] if segment]
    if entry.get('type') == 'file':
        return path_dirs, False
    root_name = entry.get('name') or (entry.get('item_path') or '').rstrip('/').rsplit('/', 1)[-1] or source_item_id
    return [root_name] + path_dirs, True


def _materialize(bind):
    """Materialize directory chains for all eligible link rows.

    Extracted from ``upgrade()`` so tests can import and call it directly
    against a plain SQLAlchemy ``Connection``.
    """
    eligible = sa.and_(
        _kf.c.relative_path.isnot(None),
        _kf.c.source_item_id.isnot(None),
        _kf.c.directory_id.is_(None),
    )

    kb_ids = [
        row[0]
        for row in bind.execute(sa.select(_kf.c.knowledge_id).where(eligible).distinct().order_by(_kf.c.knowledge_id))
    ]

    for kb_id in kb_ids:
        kb_row = bind.execute(
            sa.select(_knowledge.c.user_id, _knowledge.c.meta).where(_knowledge.c.id == kb_id)
        ).fetchone()
        if kb_row is None:
            continue
        kb_user_id = kb_row.user_id or ''
        meta = _loads_meta(kb_row.meta)
        sources = _source_map(meta)
        if not sources:
            continue

        # Pre-load this KB's existing directories for find-or-create.
        directories = {}  # (parent_id, name) -> id
        for row in bind.execute(
            sa.select(_directory.c.id, _directory.c.parent_id, _directory.c.name).where(
                _directory.c.knowledge_id == kb_id
            )
        ):
            directories.setdefault((row.parent_id, row.name), row.id)

        stamped = {}  # (meta_key, item_id) -> root directory id
        last_id = ''
        while True:
            rows = bind.execute(
                sa.select(_kf.c.id, _kf.c.user_id, _kf.c.relative_path, _kf.c.source_item_id)
                .where(eligible, _kf.c.knowledge_id == kb_id, _kf.c.id > last_id)
                .order_by(_kf.c.id)
                .limit(_BATCH_SIZE)
            ).fetchall()
            if not rows:
                break

            for row in rows:
                resolved = sources.get(row.source_item_id)
                if resolved is None:
                    continue  # unknown source: leave for the write-path bridge
                meta_key, entry = resolved
                segments, wraps_source = _segments_for(entry, row.relative_path, row.source_item_id)
                if not segments:
                    continue

                parent_id = None
                for name in segments:
                    directory_id = directories.get((parent_id, name))
                    if directory_id is None:
                        directory_id = str(uuid.uuid4())
                        now = int(time.time())
                        bind.execute(
                            _directory.insert().values(
                                id=directory_id,
                                knowledge_id=kb_id,
                                parent_id=parent_id,
                                name=name,
                                user_id=row.user_id or kb_user_id,
                                created_at=now,
                                updated_at=now,
                            )
                        )
                        directories[(parent_id, name)] = directory_id
                    parent_id = directory_id

                if wraps_source:
                    stamped[(meta_key, row.source_item_id)] = directories[(None, segments[0])]

                bind.execute(sa.update(_kf).where(_kf.c.id == row.id).values(directory_id=parent_id))

            last_id = rows[-1].id
            if len(rows) < _BATCH_SIZE:
                break

        # Record sources[].root_directory_id in the provider meta blobs.
        changed = False
        for (meta_key, item_id), root_directory_id in stamped.items():
            for entry in (meta.get(meta_key) or {}).get('sources') or []:
                if isinstance(entry, dict) and entry.get('item_id') == item_id:
                    if entry.get('root_directory_id') != root_directory_id:
                        entry['root_directory_id'] = root_directory_id
                        changed = True
                    break
        if changed:
            bind.execute(sa.update(_knowledge).where(_knowledge.c.id == kb_id).values(meta=meta))


def upgrade():
    _materialize(op.get_bind())


def downgrade():
    # No-op: see module docstring for rationale.
    pass
