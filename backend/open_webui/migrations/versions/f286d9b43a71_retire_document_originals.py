"""[Gradient] Retire OWUI document originals after platform cutover.

Revision ID: f286d9b43a71
Revises: 73c9a4d82e16
"""

import json
from pathlib import Path

import sqlalchemy as sa
from alembic import context, op

revision = 'f286d9b43a71'
down_revision = '73c9a4d82e16'
branch_labels = None
depends_on = None


def retire_documents(bind, upload_dir: Path, delete=None) -> dict[str, int]:
    from open_webui.soev.retire_document_bytes import delete_original

    delete = delete or (lambda path: delete_original(path, upload_dir))
    counts = {'legacy_documents_dropped': 0, 'document_paths_cleared': 0, 'document_objects_deleted': 0}
    inspector = sa.inspect(bind)
    if not inspector.has_table('file'):
        return counts
    files = sa.table(
        'file',
        sa.column('id', sa.Text),
        sa.column('filename', sa.Text),
        sa.column('path', sa.Text),
        sa.column('meta', sa.JSON),
    )
    skills = set()
    if inspector.has_table('skill_file'):
        skills = set(bind.execute(sa.text('SELECT file_id FROM skill_file')).scalars())

    def rows():
        last_id = None
        while True:
            query = sa.select(files).order_by(files.c.id).limit(200)
            if last_id is not None:
                query = query.where(files.c.id > last_id)
            batch = bind.execute(query).mappings().all()
            if not batch:
                return
            yield from batch
            last_id = batch[-1]['id']

    def retained(row):
        meta = row['meta'] or {}
        return (
            row['id'] in skills
            or meta.get('local_blob')
            or (meta.get('content_type') or '').startswith('image/')
            or (
                (meta.get('content_type') or '').startswith('audio/')
                and (row['filename'] or '').startswith('generated-')
            )
        )

    protected_paths = {row['path'] for row in rows() if retained(row) and row['path']}
    deleted_paths = set()
    for row in rows():
        if retained(row):
            continue
        meta = row['meta'] or {}
        path = row['path']
        if path and path not in protected_paths and path not in deleted_paths:
            delete(path)
            deleted_paths.add(path)
            counts['document_objects_deleted'] += 1
        if not meta.get('soev_collection_key') and not meta.get('source'):
            bind.execute(files.delete().where(files.c.id == row['id']))
            counts['legacy_documents_dropped'] += 1
        elif path:
            bind.execute(files.update().where(files.c.id == row['id']).values(path=None))
            counts['document_paths_cleared'] += 1
    return counts


def upgrade() -> None:
    from open_webui.env import DATA_DIR

    counts = retire_documents(op.get_bind(), DATA_DIR / 'uploads')
    context.config.attributes['document_cleanup_counts'] = counts
    print(json.dumps(counts, sort_keys=True))


def downgrade() -> None:
    pass
