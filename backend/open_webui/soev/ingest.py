"""Submit file ingest jobs, retain upload progress, and read renditions on demand."""

import asyncio
import datetime as dt
import hashlib
import posixpath
import time
from pathlib import Path
from urllib.parse import quote

from open_webui import config
from open_webui.models.files import FileModel, Files
from open_webui.soev import identity
from open_webui.soev.client import SoevApiError, SoevClient
from open_webui.storage.provider import Storage
from open_webui.utils.content_types import content_type_for

ATTACHMENTS_PREFIX = 'owui-attachments-'


class IngestBusy(Exception):
    """The file already has an ingest job in flight."""


def attachments_collection_key(user_id: str) -> str:
    return ATTACHMENTS_PREFIX + user_id


def is_attachments_collection(key: str) -> bool:
    return key.startswith(ATTACHMENTS_PREFIX)


async def _as_user(user_id: str, client: SoevClient) -> str:
    ref = f'owui:user:{user_id}'
    await identity.ensure_link(ref, client)
    return ref


async def rendition_of(file: FileModel, user_id: str, *, client: SoevClient | None = None) -> str | None:
    content = (file.data or {}).get('content')
    if content:
        return content
    if not config.SOEV_API_URL:
        return None
    client = client if client is not None else identity.build_client()
    ref = await _as_user(user_id, client)
    response = await client.get('/v1/documents', params={'source_id': file.id}, as_user=ref)
    keys = [document['collection_key'] for document in response['data']]
    keys.sort(key=lambda key: key != attachments_collection_key(user_id))
    if not keys:
        return None
    try:
        return await client.get_text(
            f'/v1/collections/{quote(keys[0], safe="")}/documents/{quote(file.id, safe="")}/content', as_user=ref
        )
    except SoevApiError as error:
        if error.status == 404 or (error.status == 409 and error.code == 'rendition_missing'):
            return None
        raise


def _read_file(file_path: str) -> bytes:
    return Path(Storage.get_file(file_path)).read_bytes()


async def ensure_attachments_collection(user_id: str, client: SoevClient) -> str:
    key = attachments_collection_key(user_id)
    ref = await _as_user(user_id, client)
    try:
        await client.get(f'/v1/collections/{quote(key, safe="")}', as_user=ref)
    except SoevApiError as error:
        if error.code != 'collection_not_found':
            raise
        await client.send(
            'POST',
            '/v1/collections',
            {
                'key': key,
                'name': 'Chat attachments',
                'visibility': 'restricted',
                'principals': [config.SOEV_API_SERVICE_PRINCIPAL, ref],
                'writers': [ref],
            },
            as_user=ref,
            idempotency_key=f'kb:{key}',
        )
    return key


async def submit(
    file: FileModel,
    *,
    collection_key: str,
    user_id: str,
    text: str | None = None,
    attempt: int = 1,
    client: SoevClient | None = None,
) -> str:
    if (file.meta or {}).get('soev_job') is not None:
        raise IngestBusy(file.id)
    current = await Files.get_file_by_id(file.id)
    if current is not None and (current.meta or {}).get('soev_job') is not None:
        raise IngestBusy(file.id)
    client = client if client is not None else identity.build_client()
    body = text.encode() if text is not None else await asyncio.to_thread(_read_file, file.path)
    digest = hashlib.sha256(body).hexdigest()
    inline = text is not None and len(body) <= config.SOEV_API_INLINE_DOCUMENT_BYTES
    meta = file.meta or {}
    name = meta.get('name') or file.filename
    path = posixpath.dirname(meta.get('relative_path') or '') or None
    document = {
        'source_id': file.id,
        'filename': name,
        'title': name,
        'content_type': 'text/plain' if text is not None else content_type_for(name, meta.get('content_type')),
    }
    if path:
        document['path'] = path
    if file.created_at is not None:
        document['created_at'] = dt.datetime.fromtimestamp(file.created_at, dt.UTC).isoformat().replace('+00:00', 'Z')
    document.update({'text': text} if inline else {'size': len(body), 'sha256': digest})
    job_key = f'ingest:{file.id}:{collection_key}:{digest}:{attempt}'
    ref = await _as_user(user_id, client)
    response = await client.send(
        'POST',
        '/v1/jobs',
        {'collection_key': collection_key, 'documents': [document]},
        as_user=ref,
        idempotency_key=job_key,
    )
    job_id = response['job_id']
    job = {
        'job_id': job_id,
        'collection_key': collection_key,
        'path': path,
        'sha256': digest,
        'attempt': attempt,
        'submitted_at': int(time.time()),
        'committed': inline,
    }
    await Files.update_file_metadata_by_id(file.id, {'soev_job': job, 'soev_collection_key': collection_key})
    await Files.set_status(file.id, 'processing')
    if not inline:
        for upload in response['uploads']:
            await client.put_bytes(upload['url'], headers=upload['headers'], body=body)
        await client.send('POST', f'/v1/jobs/{job_id}/commit', None, as_user=ref, idempotency_key=f'{job_key}:commit')
        await Files.update_file_metadata_by_id(file.id, {'soev_job': {**job, 'committed': True}})
    return job_id


async def cancel(file: FileModel, *, client: SoevClient | None = None) -> None:
    current = await Files.get_file_by_id(file.id)
    job = ((current if current is not None else file).meta or {}).get('soev_job')
    if job is None:
        return
    client = client if client is not None else identity.build_client()
    job_id = job['job_id']
    try:
        await client.send('POST', f'/v1/jobs/{job_id}/cancel', None, idempotency_key=f'ingest:{job_id}:cancel')
    except SoevApiError as error:
        if error.status != 404 and not (error.status == 409 and error.code == 'job_already_terminal'):
            raise
    await Files.update_file_metadata_by_id(file.id, {'soev_job': None, 'soev_collection_key': None})
