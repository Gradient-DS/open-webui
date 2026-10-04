"""Persist reference attachments without receiving or storing provider bytes."""

import time
from urllib.parse import quote, urlsplit

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from open_webui.models.files import FileForm, FileModel, Files
from open_webui.soev import identity, ingest
from open_webui.soev.client import SoevApiError


async def register_attachment(user_id: str, event: dict) -> FileModel:
    collection = event['collection_key']
    if collection != ingest.attachments_collection_key(user_id):
        raise ValueError('Unexpected attachment collection')
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
        raise ValueError('Attachment File identity mismatch')
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
    disposition = 'inline' if not attachment and content_type == 'application/pdf' else 'attachment'

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
