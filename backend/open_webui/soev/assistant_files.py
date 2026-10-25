"""[Claude] An assistant's own files, in a collection everyone who may use the assistant reads.

The collection is `owui-assistant-<model id>`, owned by the assistant's owner, with the readers and writers of the
assistant's access grants (projected like a knowledge base's). It is kept in step with the assistant: saving it
ingests its files that are not there yet and republishes its access, deleting it deletes the collection. A turn reads
the files there as the user, so a shared assistant's files reach every user it is shared with.
"""

import logging
from urllib.parse import quote
from uuid import uuid4

from open_webui import config
from open_webui.models.files import Files
from open_webui.models.models import ModelModel, Models
from open_webui.soev import acting, identity, ingest, knowledge_sharing, projection
from open_webui.soev.client import SoevApiError, SoevClient

log = logging.getLogger(__name__)

ASSISTANT_TAG = 'assistant-files'


def file_ids(meta: dict | None) -> list[str]:
    """The explicit file entries of a model's knowledge, in order; knowledge bases and notes are not files."""
    knowledge = (meta or {}).get('knowledge')
    return list(
        dict.fromkeys(
            entry['id']
            for entry in (knowledge if isinstance(knowledge, list) else [])
            if isinstance(entry, dict)
            and entry.get('type') == 'file'
            and isinstance(entry.get('id'), str)
            and entry['id']
        )
    )


def access(model: ModelModel, owner: str) -> dict:
    """The collection's access: the assistant's grants, with its owner reading and writing."""
    grants = knowledge_sharing.grant_dicts(model.access_grants)
    visibility, readers, writers = projection.access_of(grants, service_principal=config.SOEV_API_SERVICE_PRINCIPAL)
    return {
        'visibility': visibility,
        'principals': sorted(set(readers) | {owner}),
        'writers': sorted(set(writers) | {owner}),
    }


def _path(key: str) -> str:
    return '/v1/collections/' + quote(key, safe='')


async def ensure_collection(model: ModelModel, client: SoevClient) -> str:
    """Create the assistant's collection, or republish its access when it exists. Its idempotency keys are fresh
    per call: a collection deleted with its assistant's last file is created again later under the same key."""
    key = ingest.assistant_collection_key(model.id)
    owner = f'owui:user:{model.user_id}'
    body = access(model, owner)
    try:
        await client.get(_path(key), as_user=owner)
    except SoevApiError as error:
        if error.code != 'collection_not_found':
            raise
        await client.send(
            'POST',
            '/v1/collections',
            {'key': key, 'name': 'Assistant files', 'tags': [ASSISTANT_TAG], **body},
            as_user=owner,
            idempotency_key=f'kb:{key}:{uuid4()}',
        )
        return key
    await client.send('PUT', _path(key) + '/access', body, as_user=owner, idempotency_key=f'{key}:access:{uuid4()}')
    return key


async def _delete_collection(model_id: str, client: SoevClient) -> None:
    """Delete the collection of an assistant that is gone or has no files left, as the owner the collection
    names."""
    key = ingest.assistant_collection_key(model_id)
    collection = await existing(model_id, client=client)
    if collection is None:
        return
    if collection['created_by'] != acting.requester_ref():
        raise PermissionError('Assistant collection requires its owner request')
    await client.send(
        'DELETE',
        _path(key),
        None,
        as_user=acting.requester_ref(),
        idempotency_key=f'assistant-delete:{key}:{uuid4()}',
    )


async def sync(model_id: str, *, client: SoevClient | None = None) -> bool:
    """Bring the assistant's collection in step with the assistant: its access, its owner's files submitted where
    they are missing and every other document deleted. The collection of an assistant that is gone or has no such
    files is deleted. True while a file still ingests elsewhere and waits for another sync."""
    client = client if client is not None else identity.build_client()
    model = await Models.get_model_by_id(model_id)
    if model is not None:
        acting.require_owner(model.user_id)
    ids = file_ids(model.meta.model_dump() if model is not None and model.meta else None)
    # [Gradient] Only the owner's own files: an assistant never shares, nor touches the record of, a file someone
    # else owns (another's file reaches an assistant through a knowledge base, whose access is checked live).
    files = [file for file in await Files.get_files_by_ids(ids) if file.user_id == model.user_id] if ids else []
    if not files:
        await _delete_collection(model_id, client)
        return False
    key = await ensure_collection(model, client)
    documents = {
        document['source_id']
        async for document in client.pages(_path(key) + '/documents', as_user=acting.requester_ref())
    }
    owner = f'owui:user:{model.user_id}'
    for stale in sorted(documents - {file.id for file in files}):
        await client.send(
            'DELETE',
            f'{_path(key)}/documents/{quote(stale, safe="")}',
            None,
            as_user=owner,
            idempotency_key=f'assistant-file-delete:{key}:{stale}:{uuid4()}',
        )
    waiting = False
    for file in files:
        meta = file.meta or {}
        job = meta.get('soev_job')
        if job is not None:
            # [Claude] One File row holds one job: one landing elsewhere is waited for, one landing here is enough.
            waiting = waiting or job.get('collection_key') != key
            continue
        if file.id in documents or (meta.get('status') == 'failed' and meta.get('soev_collection_key') == key):
            continue
        try:
            await ingest.submit(file, collection_key=key, user_id=model.user_id, client=client)
        except ingest.IngestBusy:
            waiting = True
        except OSError as error:
            log.warning('Assistant file %s of %s not submitted (%s)', file.id, model_id, type(error).__name__)
    return waiting


async def existing(model_id: str, *, client: SoevClient | None = None) -> dict | None:
    """The assistant's collection read as the requester, or None when there is none."""
    client = client if client is not None else identity.build_client()
    try:
        return await client.get(_path(ingest.assistant_collection_key(model_id)), as_user=acting.requester_ref())
    except SoevApiError as error:
        if error.status == 404:
            return None
        raise


async def narrow(model_id: str, planned, *, client: SoevClient | None = None) -> None:
    """Before an owner's change commits: take away the readers, writers and files `planned` no longer has, or the
    whole collection when it holds no files. Never widens."""
    client = client if client is not None else identity.build_client()
    collection = await existing(model_id, client=client)
    if collection is None:
        return
    if collection['created_by'] != acting.requester_ref():
        raise PermissionError('Assistant collection requires its owner request')
    ids = set(file_ids(planned.meta.model_dump() if planned is not None else None))
    if not ids:
        await _delete_collection(model_id, client)
        return
    wanted = access(planned, acting.requester_ref())
    body = {
        'visibility': 'restricted',
        'principals': sorted(set(collection['principals']) & set(wanted['principals'])),
        'writers': sorted(set(collection['writers']) & set(wanted['writers'])),
    }
    key = collection['key']
    if any(body[field] != sorted(collection[field]) for field in ('principals', 'writers')):
        await client.send(
            'PUT',
            _path(key) + '/access',
            body,
            as_user=acting.requester_ref(),
            idempotency_key=f'{key}:access:{uuid4()}',
        )
    documents = {d['source_id'] async for d in client.pages(_path(key) + '/documents', as_user=acting.requester_ref())}
    for file_id in sorted(documents - ids):
        await delete_document(model_id, file_id, client=client)


async def delete_document(model_id: str, file_id: str, *, client: SoevClient | None = None) -> None:
    """Delete one file from the assistant's collection with the requester's own authority."""
    client = client if client is not None else identity.build_client()
    key = ingest.assistant_collection_key(model_id)
    try:
        await client.send(
            'DELETE',
            f'{_path(key)}/documents/{quote(file_id, safe="")}',
            None,
            as_user=acting.requester_ref(),
            idempotency_key=f'assistant-file-delete:{key}:{file_id}:{uuid4()}',
        )
    except SoevApiError as error:
        # A file not ingested there yet, or a collection not created yet, leaves nothing to delete.
        if error.status != 404:
            raise
