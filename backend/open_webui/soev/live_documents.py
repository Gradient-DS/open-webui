"""Persist reference attachments without receiving or storing provider bytes."""

import asyncio
import time
from urllib.parse import quote, urlsplit
from weakref import WeakValueDictionary

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from open_webui.models.files import FileForm, FileModel, Files
from open_webui.soev import identity, ingest
from open_webui.soev.client import SoevApiError


class AttachmentMismatch(Exception):
    def __init__(self, status: int, detail: str):
        self.status = status
        super().__init__(detail)


_collections: dict[str, str] = {}
_collection_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()


async def attachment_collection(user_id: str, client) -> str:
    if user_id in _collections:
        return _collections[user_id]
    async with _collection_locks.setdefault(user_id, asyncio.Lock()):
        if user_id not in _collections:
            _collections[user_id] = await ingest.ensure_attachments_collection(user_id, client)
        return _collections[user_id]


async def register_attachment(user_id: str, event: dict) -> FileModel:
    collection = event['collection_key']
    if collection != ingest.attachments_collection_key(user_id):
        raise AttachmentMismatch(409, 'Unexpected attachment collection')
    existing = await Files.get_file_by_id(event['source_id'])
    if existing is None:
        url = event.get('web_url') or ''
        if urlsplit(url).scheme != 'https':
            url = ''
        existing = await Files.insert_new_file(
            user_id,
            FileForm(
                id=event['source_id'],
                filename=event['name'],
                path='',
                data={'status': 'processing'},
                meta={
                    'name': event['name'],
                    'content_type': event.get('content_type'),
                    'status': 'processing',
                    'collection_name': collection,
                    'soev_collection_key': collection,
                    'source': {'provider': event['provider'], 'ref': event['provider_ref']},
                    'web_url': url,
                    'attached_by': event['attached_by'],
                    'soev_job': {
                        'kind': 'reference',
                        'job_id': event['job_id'],
                        'collection_key': collection,
                        'submitted_at': int(time.time()),
                    },
                },
            ),
        )
        if existing is None:
            existing = await Files.get_file_by_id(event['source_id'])
    if existing is None:
        raise RuntimeError('Attachment File write failed')
    if existing.user_id != user_id or (existing.meta or {}).get('collection_name') != collection:
        raise AttachmentMismatch(403, 'Attachment File identity mismatch')
    return existing


def chat_file(file: FileModel) -> dict:
    meta = file.meta or {}
    return {
        'type': 'file',
        'id': file.id,
        'name': file.filename,
        'collection_name': meta['collection_name'],
        'status': 'uploaded' if meta['status'] == 'completed' else meta['status'],
        'source': meta['source'],
        'web_url': meta['web_url'],
        'attached_by': meta['attached_by'],
    }


async def stream_original(file: FileModel, user, *, attachment: bool):
    client = identity.build_client()
    ref = await identity.acting_ref(user, client)
    collection = quote(file.meta['collection_name'], safe='')
    path = f'/v1/collections/{collection}/documents/{quote(file.id, safe="")}/original'
    stream = client.stream(path, as_user=ref)
    try:
        first = await anext(stream, b'')
    except SoevApiError as error:
        await stream.aclose()
        raise HTTPException(status_code=error.status, detail=error.detail) from None
    content_type = file.meta.get('content_type') or 'application/octet-stream'
    disposition = (
        'inline'
        if not attachment and (content_type == 'application/pdf' or content_type.startswith('image/'))
        else 'attachment'
    )

    async def chunks():
        try:
            yield first
            async for chunk in stream:
                yield chunk
        finally:
            await stream.aclose()

    return StreamingResponse(
        chunks(),
        media_type=content_type,
        headers={'Content-Disposition': f"{disposition}; filename*=UTF-8''{quote(file.filename, safe='')}"},
        background=BackgroundTask(stream.aclose),
    )
