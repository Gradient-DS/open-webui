"""Resolve original documents with the reader's platform authority."""

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from open_webui import config
from open_webui.models.files import Files
from open_webui.soev import identity
from open_webui.soev.client import SoevApiError
from open_webui.storage.provider import Storage
from open_webui.utils.upload_guard import sniff_content_type


def path_for(collection: str, source_id: str) -> str:
    return f'/v1/collections/{quote(collection, safe="")}/documents/{quote(source_id, safe="")}/original'


async def lookup(file, client, *, as_user: str | None) -> str | None:
    keys = {
        row['collection_key']
        async for row in client.pages('/v1/documents', params={'source_id': file.id}, as_user=as_user)
        if row.get('source_id', file.id) == file.id and isinstance(row.get('collection_key'), str)
    }
    preferred = 'owui-attachments-' + file.user_id
    return next(iter(sorted(keys, key=lambda key: (key != preferred, key))), None)


def read_collection(file) -> str | None:
    meta = file.meta or {}
    return meta.get('soev_read_collection') or meta.get('collection_name') or meta.get('soev_collection_key')


async def remember(file, collection: str) -> None:
    await Files.update_file_metadata_by_id(file.id, {'soev_read_collection': collection})
    file.meta = {**(file.meta or {}), 'soev_read_collection': collection}


async def published(file_id: str, collection: str) -> None:
    from sqlalchemy import select, update
    from open_webui.models import files

    async with files.get_async_db_context() as session:
        row = await session.scalar(select(files.File).where(files.File.id == file_id).with_for_update())
        if row is None:
            raise RuntimeError('File disappeared before publication')
        meta = row.meta or {}
        if not meta.get('soev_read_collection'):
            key = meta.get('collection_name') or collection
            # The conditional write also protects SQLite, where FOR UPDATE is ignored.
            await session.execute(
                update(files.File)
                .where(
                    files.File.id == file_id,
                    files.File.meta['soev_read_collection'].as_string().is_(None),
                )
                .values(meta={**meta, 'soev_read_collection': key, 'collection_name': key})
            )
            await session.commit()


@asynccontextmanager
async def open_original(file, *, client=None, as_user: str, range_header: str | None = None):
    client = client or identity.build_client()
    collection = read_collection(file)
    async with AsyncExitStack() as stack:
        response = None
        if collection:
            try:
                response = await stack.enter_async_context(
                    client.stream_response(
                        path_for(collection, file.id),
                        as_user=as_user,
                        **({'range_header': range_header} if range_header is not None else {}),
                    )
                )
            except SoevApiError as error:
                if error.status != 404:
                    raise
        if response is None:
            collection = await lookup(file, client, as_user=as_user)
            if collection is None:
                raise SoevApiError(404, 'file_not_found', 'Original unavailable', constraint='upload:original')
            response = await stack.enter_async_context(
                client.stream_response(
                    path_for(collection, file.id),
                    as_user=as_user,
                    **({'range_header': range_header} if range_header is not None else {}),
                )
            )
            await remember(file, collection)
        yield response


def local_prefix(file) -> bytes:
    if not file.path:
        return b''
    path = Path(Storage.get_file(file.path))
    with path.open('rb') as source:
        return source.read(64 * 1024)


async def prefer_local(file) -> bool:
    meta = file.meta or {}
    if meta.get('local_blob') or meta.get('local_kind') in ('image', 'video', 'skill', 'generated'):
        return True
    if (meta.get('content_type') or '').startswith('video/'):
        return True
    if (meta.get('content_type') or '').startswith('audio/') and file.filename.startswith('generated-'):
        return True
    mime = meta.get('content_type') or ''
    if mime and mime != 'application/octet-stream' and not mime.startswith('image/'):
        return False
    try:
        detected = await asyncio.to_thread(lambda: sniff_content_type(local_prefix(file)))
    except (OSError, RuntimeError, ValueError):
        return False
    return detected.startswith(('image/', 'video/'))


async def proxy_original(file, user, *, attachment: bool, range_header: str | None = None):
    if not config.SOEV_API_URL or await prefer_local(file):
        return None
    from fastapi import HTTPException
    from open_webui.soev.downloads import proxy_download

    meta = file.meta or {}
    reference = bool(meta.get('source'))
    try:
        return await proxy_download(
            open_original(file, as_user=identity.external_ref(user), range_header=range_header),
            filename=file.filename,
            content_type=meta.get('content_type') or 'application/octet-stream',
            attachment=attachment,
        )
    except SoevApiError as error:
        if error.status == 404:
            if meta.get('status') in ('pending', 'processing'):
                raise HTTPException(409, detail='original_processing') from None
            if not reference and file.path:
                return None
        raise HTTPException(error.status, detail=error.detail) from None
