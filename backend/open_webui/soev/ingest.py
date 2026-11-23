"""Submit file ingest jobs, retain upload progress, and read renditions on demand."""

import asyncio
import datetime as dt
import hashlib
import posixpath
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import BinaryIO
from urllib.parse import quote

from open_webui import config
from open_webui.models.files import FileModel, Files
from open_webui.soev import acting, identity, copy_state
from open_webui.soev.client import SoevApiError, SoevClient, StoreTransferError
from open_webui.soev.principal_refs import person_ref
from open_webui.utils.content_types import content_type_for

ATTACHMENTS_PREFIX = 'owui-attachments-'
ASSISTANT_PREFIX = 'owui-assistant-'
FOLDER_PREFIX = 'owui-folder-'
ATTACHMENTS_TAG = 'chat-attachments'
UPLOAD_CHUNK_BYTES = 64 * 1024

_active_transfers: set[str] = set()


def transfer_active(file_id: str) -> bool:
    return file_id in _active_transfers


def upload_deadline(job: dict) -> float:
    value = job.get('expires_at')
    if isinstance(value, str):
        return dt.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    if isinstance(value, (int, float)):
        return value
    return job['submitted_at'] + 15 * 60


def copying(file, *, text=None, source=None) -> bool:
    meta = file.meta or {}
    return (
        source is None
        and text is None
        and (meta.get('status') == 'completed' or bool(meta.get('soev_read_collection')))
    )


_request_source: ContextVar[tuple[str, BinaryIO] | None] = ContextVar('document_upload_source', default=None)


@contextmanager
def uploading(file_id: str, source: BinaryIO) -> Iterator[None]:
    token = _request_source.set((file_id, source))
    try:
        yield
    finally:
        _request_source.reset(token)


def source_for(file_id: str) -> BinaryIO | None:
    current = _request_source.get()
    return current[1] if current is not None and current[0] == file_id else None


class IngestBusy(Exception):
    """The file already has an ingest job in flight."""


def attachments_collection_key(user_id: str) -> str:
    return ATTACHMENTS_PREFIX + user_id


def is_attachments_collection(key: str) -> bool:
    return key.startswith(ATTACHMENTS_PREFIX)


def assistant_collection_key(model_id: str) -> str:
    """[Claude] The collection holding an assistant's own files (`soev/assistant_files.py`). A model id may run to
    256 characters, a collection key to 128: a longer one goes by its digest."""
    key = ASSISTANT_PREFIX + model_id
    return key if len(key) <= 128 else ASSISTANT_PREFIX + hashlib.sha256(model_id.encode()).hexdigest()


def is_assistant_collection(key: str) -> bool:
    return key.startswith(ASSISTANT_PREFIX)


def folder_collection_key(folder_id: str) -> str:
    """[Gradient] The collection holding a shared folder's own files (`soev/folder_files.py`)."""
    return FOLDER_PREFIX + folder_id


def is_folder_collection(key: str) -> bool:
    return key.startswith(FOLDER_PREFIX)


def is_chat_attachment(entry: dict) -> bool:
    """A chat `files` entry the agent reads as a document; an image goes to the model as pixels instead."""
    return (
        entry.get('type') == 'file'
        and bool(entry.get('id'))
        and not (entry.get('content_type') or '').startswith('image/')
    )


async def rendition_of(file: FileModel, user_id: str, *, client: SoevClient | None = None) -> str | None:
    if client is not None and client.is_service:
        raise PermissionError('File jobs require user authority')
    content = (file.data or {}).get('content')
    if content:
        return content
    if not config.SOEV_API_URL:
        return None
    client = client if client is not None else identity.build_client()
    if client.is_service:
        raise PermissionError('File jobs require user authority')
    ref = f'owui:user:{user_id}'
    keys = [
        document['collection_key']
        async for document in client.pages('/v1/documents', params={'source_id': file.id}, as_user=ref)
    ]
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


async def _source_chunks(source: BinaryIO) -> AsyncIterator[bytes]:
    await asyncio.to_thread(source.seek, 0)
    while chunk := await asyncio.to_thread(source.read, UPLOAD_CHUNK_BYTES):
        yield chunk


def _encoded_text(text: str) -> Iterator[bytes]:
    for offset in range(0, len(text), UPLOAD_CHUNK_BYTES // 4):
        yield text[offset : offset + UPLOAD_CHUNK_BYTES // 4].encode()


def _text_info(text: str) -> tuple[int, str]:
    digest, size = hashlib.sha256(), 0
    for chunk in _encoded_text(text):
        digest.update(chunk)
        size += len(chunk)
    return size, digest.hexdigest()


async def _text_chunks(text: str) -> AsyncIterator[bytes]:
    for chunk in _encoded_text(text):
        yield chunk


async def ensure_attachments_collection(user_id: str, client: SoevClient) -> str:
    key = attachments_collection_key(user_id)
    ref = f'owui:user:{user_id}'
    owner = await person_ref(user_id)
    try:
        collection = await client.get(f'/v1/collections/{quote(key, safe="")}', as_user=ref)
    except SoevApiError as error:
        if error.status != 404:
            raise
        await client.send(
            'POST',
            '/v1/collections',
            {
                'key': key,
                'name': 'Chat attachments',
                'personal': True,
                'visibility': 'restricted',
                'principals': [config.SOEV_API_SERVICE_PRINCIPAL, owner],
                'writers': [owner],
                'tags': [ATTACHMENTS_TAG],
            },
            as_user=ref,
            idempotency_key=f'kb:{key}',
        )
    else:
        tags = collection.get('tags') or []
        if ATTACHMENTS_TAG not in tags:
            await client.send(
                'PATCH',
                f'/v1/collections/{quote(key, safe="")}',
                {'tags': [*tags, ATTACHMENTS_TAG]},
                as_user=ref,
                idempotency_key=f'kb:{key}:tag:{ATTACHMENTS_TAG}',
            )
    return key


async def _submit(
    file: FileModel,
    *,
    collection_key: str,
    user_id: str,
    text: str | None = None,
    source: BinaryIO | None = None,
    attempt: int | None = None,
    client: SoevClient | None = None,
    copy: bool = False,
) -> str:
    if (file.meta or {}).get('soev_job') is not None:
        raise IngestBusy(file.id)
    current = await Files.get_file_by_id(file.id)
    if current is not None and (current.meta or {}).get('soev_job') is not None:
        raise IngestBusy(file.id)
    client = client if client is not None else identity.build_client()
    if client.is_service:
        raise PermissionError('File jobs require user authority')
    meta = (current or file).meta or {}
    attempt = attempt if attempt is not None else int(meta.get('soev_attempt', 0)) + 1
    await Files.update_file_metadata_by_id(file.id, {'soev_attempt': attempt})
    if text is None:
        size, digest = meta.get('size'), meta.get('file_hash')
        if not isinstance(size, int) or size < 1 or not isinstance(digest, str):
            raise SoevApiError(502, 'upload_declaration_invalid', 'Invalid upload declaration')
    else:
        size, digest = await asyncio.to_thread(_text_info, text)
    inline = text is not None and size <= config.SOEV_API_INLINE_DOCUMENT_BYTES
    name = meta.get('name') or file.filename
    path = posixpath.dirname(meta.get('relative_path') or '') or None
    document = {
        'source_id': file.id,
        'filename': name,
        'title': name,
        'content_type': 'text/plain'
        if text is not None
        else meta.get('upload_content_type')
        or (
            meta.get('content_type')
            if meta.get('local_kind') in ('image', 'video')
            else content_type_for(name, meta.get('content_type'))
        ),
    }
    if path:
        document['path'] = path
    if file.created_at is not None:
        document['created_at'] = dt.datetime.fromtimestamp(file.created_at, dt.UTC).isoformat().replace('+00:00', 'Z')
    document.update({'text': text} if inline else {'size': size, 'sha256': digest})
    job_key = f'ingest:{file.id}:{collection_key}:{digest}:{attempt}'
    ref = f'owui:user:{user_id}'
    body = {'collection_key': collection_key, 'documents': [document]}
    if source is None and text is None and not meta.get('soev_read_collection'):
        from open_webui.soev.originals import lookup, remember

        origin = await lookup(file, client, as_user=ref)
        if origin is not None:
            await remember(file, origin)

    async def create():
        return await client.send('POST', '/v1/jobs', body, as_user=ref, idempotency_key=job_key)

    async def chunks():
        if text is not None:
            async for chunk in _text_chunks(text):
                yield chunk
        elif source is not None:
            async for chunk in _source_chunks(source):
                yield chunk
        else:
            from open_webui.soev.originals import open_original

            async with open_original(file, client=client, as_user=ref) as response:
                async for chunk in response.aiter_bytes():
                    yield chunk

    # Persist the declaration before admission: a timed-out create can be replayed and cancelled.
    await Files.update_file_metadata_by_id(
        file.id,
        {
            'soev_job': {
                'as_user': ref,
                'collection_key': collection_key,
                'create_body': body,
                'create_key': job_key,
                'attempt': attempt,
                'sha256': digest,
                'submitted_at': int(time.time()),
                'expires_at': time.time() + 15 * 60,
                'copy': copy,
            }
        },
    )
    try:
        response = await create()
    except SoevApiError as error:
        if 400 <= error.status < 500 and error.status not in (408, 429):
            await Files.update_file_metadata_by_id(file.id, {'soev_job': None})
        raise
    job_id = response['job_id']
    if not collection_key.startswith((ATTACHMENTS_PREFIX, ASSISTANT_PREFIX, FOLDER_PREFIX)):
        from open_webui.soev.file_collections import knowledge_link

        await knowledge_link(file.id, collection_key)
    job = {
        'as_user': ref,
        'job_id': job_id,
        'collection_key': collection_key,
        'path': path,
        'sha256': digest,
        'attempt': attempt,
        'submitted_at': int(time.time()),
        'committed': inline,
        'expires_at': response.get('expires_at') or time.time() + 15 * 60,
        'copy': copy,
    }
    metadata = {'soev_job': job}
    if not copy:
        metadata['soev_collection_key'] = collection_key
    await Files.update_file_metadata_by_id(file.id, metadata)
    if copy:
        await copy_state.record(file, collection_key, 'processing')
    else:
        await Files.set_status(file.id, 'processing')
    if not inline:
        try:
            for refresh in range(2):
                if response['status'] != 'AWAITING_UPLOAD':
                    break
                uploads = response.get('uploads', [])
                if (
                    not isinstance(uploads, list)
                    or len(uploads) != 1
                    or not isinstance(uploads[0], dict)
                    or uploads[0].get('source_id') != file.id
                ):
                    raise SoevApiError(502, 'upload_declaration_invalid', 'Invalid upload declaration')
                upload = uploads[0]
                try:
                    expires = dt.datetime.fromisoformat(upload['expires_at'].replace('Z', '+00:00'))
                    expired = expires <= dt.datetime.now(dt.UTC)
                except (KeyError, ValueError, TypeError, AttributeError):
                    raise SoevApiError(502, 'upload_declaration_invalid', 'Invalid upload declaration') from None
                try:
                    if expired:
                        raise StoreTransferError(403, 'upload_rejected', 'Upload rejected')
                    await client.put_transfer(upload, chunks, size=size, sha256=digest)
                    break
                except StoreTransferError as error:
                    if error.status != 403 or refresh:
                        raise
                    response = await create()
                    if response['job_id'] != job_id:
                        raise SoevApiError(502, 'upload_declaration_invalid', 'Invalid upload declaration') from None
        except SoevApiError as error:
            raise
        await client.send(
            'POST', f'/v1/jobs/{quote(job_id, safe="")}/commit', None, as_user=ref, idempotency_key=f'{job_key}:commit'
        )
        await Files.update_file_metadata_by_id(file.id, {'soev_job': {**job, 'committed': True}})
    return job_id


async def successful(file, job, *, client=None) -> None:
    client = client or identity.build_client()
    if client.is_service:
        raise PermissionError('File jobs require user authority')
    ref = job.get('as_user')
    if not ref or acting.requester_ref() != ref:
        raise PermissionError('Job completion requires its creator')
    if job.get('copy'):
        await copy_state.record(file, job['collection_key'], 'completed')
    else:
        await Files.set_status(file.id, 'completed')
        from open_webui.soev.originals import published

        try:
            document = await client.get(
                f'/v1/collections/{quote(job["collection_key"], safe="")}/documents/{quote(file.id, safe="")}',
                as_user=ref,
            )
            await published(file.id, job['collection_key'], document)
        except SoevApiError:
            # Success is authoritative; an unreadable original is recovered under the reader's authority later.
            pass
    await Files.update_file_metadata_by_id(file.id, {'soev_job': None})


async def abort(
    file: FileModel, *, client=None, error: str = 'upload_failed', collection_key: str | None = None, copy: bool = False
) -> bool:
    if client is not None and client.is_service:
        raise PermissionError('File jobs require user authority')
    current = await Files.get_file_by_id(file.id)
    current = current or file
    job = (current.meta or {}).get('soev_job')
    is_copy = job.get('copy', False) if job else copy
    target = job['collection_key'] if job else collection_key
    if is_copy and target:
        await copy_state.record(current, target, 'failed', error=error)
    else:
        await Files.set_status(file.id, 'failed', error=error)
    if not job:
        return False
    await Files.update_file_metadata_by_id(file.id, {'soev_job': {**job, 'cancel_pending': True}})
    try:
        succeeded = await cancel(current, client=client, allow_active=True)
        if not is_copy and not succeeded:
            await Files.update_file_metadata_by_id(file.id, {'soev_collection_key': target})
        return succeeded
    except Exception:
        # Retry cancellation in the owner's next authenticated reconciliation.
        return False


async def submit(file: FileModel, **kwargs) -> str:
    if kwargs.get('client') is not None and kwargs['client'].is_service:
        raise PermissionError('File jobs require user authority')
    if transfer_active(file.id):
        raise IngestBusy(file.id)
    _active_transfers.add(file.id)
    is_copy = copying(file, text=kwargs.get('text'), source=kwargs.get('source'))
    try:
        return await _submit(file, copy=is_copy, **kwargs)
    except IngestBusy:
        raise
    except BaseException as error:
        code = error.code if isinstance(error, SoevApiError) else 'upload_failed'
        succeeded = await asyncio.shield(
            abort(file, client=kwargs.get('client'), error=code, collection_key=kwargs['collection_key'], copy=is_copy)
        )
        if succeeded and not isinstance(error, asyncio.CancelledError):
            return (await Files.get_file_by_id(file.id)).meta.get('soev_last_job') or ''
        raise
    finally:
        _active_transfers.discard(file.id)


async def cancel(
    file: FileModel, *, client: SoevClient | None = None, allow_active: bool = False, now: int | None = None
) -> bool:
    if client is not None and client.is_service:
        raise PermissionError('File jobs require user authority')
    current = await Files.get_file_by_id(file.id)
    job = ((current if current is not None else file).meta or {}).get('soev_job')
    if job is None:
        return False
    client = client if client is not None else identity.build_client()
    if client.is_service:
        raise PermissionError('File jobs require user authority')
    ref = job.get('as_user')
    if not ref or acting.requester_ref() != ref or (transfer_active(file.id) and not allow_active):
        await Files.update_file_metadata_by_id(file.id, {'soev_job': {**job, 'cancel_pending': True}})
        return False
    job = await Files.claim_soev_job_cancel(
        file.id, ref.removeprefix('owui:user:'), now=int(time.time()) if now is None else now
    )
    if job is None:
        return False
    job_id = job.get('job_id')
    if job_id is None:
        try:
            response = await client.send(
                'POST', '/v1/jobs', job['create_body'], as_user=ref, idempotency_key=job['create_key']
            )
        except SoevApiError as error:
            if 400 <= error.status < 500 and error.status not in (408, 429):
                if not job.get('copy'):
                    await Files.set_status(file.id, 'failed', error=error.code)
                await Files.update_file_metadata_by_id(file.id, {'soev_job': None})
                return False
            raise
        job_id = response['job_id']
    try:
        await client.send(
            'POST',
            f'/v1/jobs/{quote(job_id, safe="")}/cancel',
            None,
            as_user=ref,
            idempotency_key=f'ingest:{job_id}:cancel',
        )
    except SoevApiError as error:
        if error.status == 409 and error.code == 'job_already_terminal':
            result = await client.get(f'/v1/jobs/{quote(job_id, safe="")}', as_user=ref)
            if result['status'] == 'SUCCEEDED':
                await Files.update_file_metadata_by_id(file.id, {'soev_last_job': job_id})
                if current is not None:
                    await successful(current, job, client=client)
                else:
                    await Files.update_file_metadata_by_id(file.id, {'soev_job': None})
                return True
        elif error.status != 404:
            raise
    metadata = {'soev_job': None}
    if not job.get('copy'):
        metadata['soev_collection_key'] = None
    await Files.update_file_metadata_by_id(file.id, metadata)
    return False
