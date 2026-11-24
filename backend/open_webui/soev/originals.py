"""Resolve original documents with the reader's platform authority."""

import asyncio
import re
from contextlib import asynccontextmanager
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


def fingerprint(file) -> tuple[int, str] | None:
    meta = file.meta or {}
    size, digest = meta.get('size'), meta.get('file_hash')
    if type(size) is int and size >= 0 and isinstance(digest, str) and re.fullmatch('[0-9a-f]{64}', digest):
        return size, digest
    return None


def matches(file, document: dict) -> bool:
    recorded = fingerprint(file)
    return bool(
        recorded
        and type(document.get('byte_size')) is int
        and (document.get('byte_size'), document.get('sha256')) == recorded
    )


def _read_matches(file, document: dict) -> bool:
    recorded = fingerprint(file)
    # The public catalog permits absent size; the download must then supply it.
    return matches(file, document) or bool(
        recorded and document.get('byte_size') is None and document.get('sha256') == recorded[1]
    )


def _download_size(response) -> int | None:
    if response.status_code == 200:
        value = response.headers.get('content-length', '')
        return int(value) if re.fullmatch('[0-9]{1,20}', value) else None
    if response.status_code in (206, 416):
        match = re.fullmatch(r'bytes (?:[0-9]+-[0-9]+|\*)/([0-9]{1,20})', response.headers.get('content-range', ''))
        return int(match[1]) if match else None
    return None


async def candidates(file, client, *, as_user: str | None, session=None, for_read=False) -> tuple[list[str], bool]:
    from open_webui.soev.file_collections import collections_for

    allowed = await collections_for(file, session=session)
    keys, seen = set(), False
    async for row in client.pages('/v1/documents', params={'source_id': file.id}, as_user=as_user):
        if row.get('source_id') != file.id:
            continue
        seen = True
        key = row.get('collection_key')
        if key in allowed and (_read_matches(file, row) if for_read else matches(file, row)):
            keys.add(key)
    preferred = 'owui-attachments-' + file.user_id
    return sorted(keys, key=lambda key: (key != preferred, key)), seen


async def lookup(file, client, *, as_user: str | None, for_read=False) -> str | None:
    if fingerprint(file) is None:
        return None
    keys, _ = await candidates(file, client, as_user=as_user, for_read=for_read)
    return next(iter(keys), None)


def read_collection(file) -> str | None:
    return (file.meta or {}).get('soev_read_collection')


async def remember(file, collection: str) -> None:
    # Callers supply a locally derived collection verified against the recorded fingerprint.
    from sqlalchemy import select, update
    from open_webui.models import files

    async with files.get_async_db_context() as session:
        row = await session.scalar(select(files.File).where(files.File.id == file.id).with_for_update())
        if row is None:
            raise RuntimeError('File disappeared before publication')
        if not (row.meta or {}).get('soev_read_collection'):
            if fingerprint(row) != fingerprint(file):
                raise RuntimeError('Original fingerprint changed during recovery')
            await session.execute(
                update(files.File)
                .where(
                    files.File.id == file.id,
                    files.File.meta['soev_read_collection'].as_string().is_(None),
                )
                .values(meta={**(row.meta or {}), 'soev_read_collection': collection})
            )
            await session.commit()
            file.meta = {**(file.meta or {}), 'soev_read_collection': collection}


async def published(file_id: str, collection: str, document: dict | None = None) -> None:
    from open_webui.soev.file_collections import collections_for

    file = await Files.get_file_by_id(file_id)
    if (
        file is not None
        and document is not None
        and matches(file, document)
        and collection in await collections_for(file)
    ):
        await remember(file, collection)


@asynccontextmanager
async def open_original(file, *, client=None, as_user: str, range_header: str | None = None):
    from open_webui.soev.file_collections import collections_for

    client = client or identity.build_client()
    collection = read_collection(file)
    verified = False
    document = {}
    if fingerprint(file) is None:
        raise SoevApiError(404, 'file_not_found', 'Original unavailable', constraint='upload:original')
    if collection and collection in await collections_for(file):
        try:
            document = await client.get(path_for(collection, file.id).removesuffix('/original'), as_user=as_user)
            verified = _read_matches(file, document)
        except SoevApiError as error:
            if error.status != 404:
                raise
    if not verified:
        collection = await lookup(file, client, as_user=as_user, for_read=True)
    if collection is None or (not verified and collection == read_collection(file)):
        # A mismatched pinned version cannot be bypassed by a list response for the same collection.
        raise SoevApiError(404, 'file_not_found', 'Original unavailable', constraint='upload:original')
    if not verified:
        document = await client.get(path_for(collection, file.id).removesuffix('/original'), as_user=as_user)
        if not _read_matches(file, document):
            raise SoevApiError(404, 'file_not_found', 'Original unavailable', constraint='upload:original')
    async with client.stream_response(
        path_for(collection, file.id),
        as_user=as_user,
        **({'range_header': range_header} if range_header is not None else {}),
    ) as response:
        if document.get('byte_size') is None and _download_size(response) != fingerprint(file)[0]:
            raise SoevApiError(404, 'file_not_found', 'Original unavailable', constraint='upload:original')
        if not read_collection(file):
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
    if meta.get('local_blob') or meta.get('local_kind') in ('skill', 'generated'):
        return True
    if (meta.get('content_type') or '').startswith('audio/') and file.filename.startswith('generated-'):
        return True
    try:
        detected = await asyncio.to_thread(lambda: sniff_content_type(local_prefix(file)))
    except (OSError, RuntimeError, ValueError):
        return False
    return detected.startswith(('image/', 'video/'))


async def refuse_original_replacement(file, request) -> None:
    if not config.SOEV_API_URL or await prefer_local(file):
        return
    meta = file.meta or {}
    known = any(meta.get(key) for key in ('soev_read_collection', 'soev_collection_key', 'soev_job', 'source'))
    from open_webui.soev.acting import requester_ref

    if known or await lookup(file, identity.build_client(), as_user=requester_ref()):
        from fastapi import HTTPException
        from open_webui.soev.errors import problem_message

        error = SoevApiError(409, 'original_immutable', 'Original is immutable')
        raise HTTPException(409, detail=problem_message(error, request.headers.get('accept-language', 'en-US')))


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
