"""[Gradient] strip File storage paths embedded in chats, shares, folders, models and channel messages

Revision ID: 901d1275cce6
Revises: c3d8e2f17a90
Create Date: 2026-10-21 09:00:00.000000

Upload responses used to carry the File row's `path` (bucket and object key, or a local path); the
frontend stored that object in message files, folder data and model knowledge. This removes `path` from
File records under a `file` key or in a `files` list. Rows are read in id-ordered batches, prefiltered on
the text `"path"`, rewritten in Python and updated by id, so it runs the same on SQLite and PostgreSQL.
Downgrade is a no-op: the paths are not needed and are still on the file rows.
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '901d1275cce6'
down_revision: str | None = 'c3d8e2f17a90'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BATCH_SIZE = 100
_FILE_RECORD_KEYS = frozenset({'id', 'user_id', 'filename', 'path'})

# (table, id column, JSON column, stored as text-encoded JSON)
_TARGETS = (
    ('chat', 'id', 'chat', False),
    ('shared_chat', 'id', 'chat', False),
    ('chat_message', 'id', 'files', False),
    ('folder', 'id', 'data', False),
    ('message', 'id', 'data', False),
    ('model', 'id', 'meta', True),
)


def _is_file_record(value) -> bool:
    return isinstance(value, dict) and _FILE_RECORD_KEYS <= value.keys()


def _without_path(record: dict) -> dict:
    return {key: value for key, value in record.items() if key != 'path'}


def _strip(value):
    """Return `value` without embedded File paths; the same object when nothing changed."""
    if isinstance(value, list):
        items = [_strip(item) for item in value]
        return items if any(new is not old for new, old in zip(items, value)) else value
    if not isinstance(value, dict):
        return value
    changed = False
    result = {}
    for key, item in value.items():
        new = _strip(item)
        if key == 'file' and _is_file_record(new):
            new = _without_path(new)
        elif key == 'files' and isinstance(new, list) and any(_is_file_record(f) for f in new):
            new = [_without_path(f) if _is_file_record(f) else f for f in new]
        changed = changed or new is not item
        result[key] = new
    return result if changed else value


def _strip_table(bind, table_name: str, id_name: str, column_name: str, text_encoded: bool) -> None:
    if not sa.inspect(bind).has_table(table_name):
        return
    column_type = sa.Text() if text_encoded else sa.JSON()
    table = sa.table(table_name, sa.column(id_name, sa.Text()), sa.column(column_name, column_type))
    row_id, column = table.c[id_name], table.c[column_name]
    last_id = None
    while True:
        query = sa.select(row_id, column).where(sa.cast(column, sa.Text).like('%"path"%'))
        if last_id is not None:
            query = query.where(row_id > last_id)
        rows = bind.execute(query.order_by(row_id).limit(_BATCH_SIZE)).fetchall()
        for key, stored in rows:
            value = json.loads(stored) if text_encoded and isinstance(stored, str) else stored
            # chat_message.files is a bare list; wrap it so its items are treated as a `files` list.
            stripped = _strip({'files': value})['files'] if column_name == 'files' else _strip(value)
            if stripped is not value:
                new = json.dumps(stripped) if text_encoded else stripped
                bind.execute(sa.update(table).where(row_id == key).values({column_name: new}))
        if len(rows) < _BATCH_SIZE:
            break
        last_id = rows[-1][0]


def strip_file_paths(bind) -> None:
    for target in _TARGETS:
        _strip_table(bind, *target)


def upgrade() -> None:
    strip_file_paths(op.get_bind())


def downgrade() -> None:
    pass
