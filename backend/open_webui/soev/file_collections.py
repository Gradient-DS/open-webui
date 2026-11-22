"""Derive a file's possible platform collections from local ownership and links."""

import json
from contextlib import AsyncExitStack

import sqlalchemy as sa

from open_webui.models import files
from open_webui.soev.ingest import attachments_collection_key, assistant_collection_key, folder_collection_key


def _object(value):
    return json.loads(value) if isinstance(value, str) else value or {}


async def collections_for(file, *, session=None) -> set[str]:
    keys = {attachments_collection_key(file.user_id)}
    async with AsyncExitStack() as stack:
        db = session if session is not None else await stack.enter_async_context(files.get_async_db_context())
        stored = await db.get(files.File, file.id)
        if stored is not None:
            keys.update((stored.meta or {}).get('soev_kb_links') or [])
        connection = await db.connection()
        tables = await connection.run_sync(lambda bind: set(sa.inspect(bind).get_table_names()))
        if 'knowledge_file' in tables:
            table = sa.table('knowledge_file', sa.column('file_id'), sa.column('knowledge_id'))
            keys.update(await db.scalars(sa.select(table.c.knowledge_id).where(table.c.file_id == file.id)))
        for name, field, derive in [
            ('model', 'meta', assistant_collection_key),
            ('folder', 'data', folder_collection_key),
        ]:
            if name not in tables:
                continue
            table = sa.table(name, sa.column('id'), sa.column('user_id'), sa.column(field, sa.JSON))
            rows = await db.execute(sa.select(table.c.id, table.c[field]).where(table.c.user_id == file.user_id))
            for key, value in rows:
                content = _object(value)
                entries = content.get('knowledge' if name == 'model' else 'files') or []
                if any(
                    isinstance(item, dict) and item.get('type') == 'file' and item.get('id') == file.id
                    for item in entries
                ):
                    keys.add(derive(key))
    return {key for key in keys if isinstance(key, str) and key}


async def knowledge_link(file_id: str, collection: str, *, remove: bool = False) -> None:
    async with files.get_async_db_context() as session:
        row = await session.scalar(sa.select(files.File).where(files.File.id == file_id).with_for_update())
        if row is None:
            return
        links = set((row.meta or {}).get('soev_kb_links') or [])
        if remove:
            links.discard(collection)
        else:
            links.add(collection)
        row.meta = {**(row.meta or {}), 'soev_kb_links': sorted(links)}
        await session.commit()
