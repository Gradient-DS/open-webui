"""[Claude] An assistant's own files, in a collection everyone who may use the assistant reads.

The collection is `owui-assistant-<model id>`, owned by the assistant's owner, with the readers and writers of the
assistant's access grants (projected like a knowledge base's). It is kept in step with the assistant: saving it
ingests its files that are not there yet and republishes its access, deleting it deletes the collection. A turn reads
the files there as the user, so a shared assistant's files reach every user it is shared with.
"""

import asyncio
import logging
from urllib.parse import quote
from uuid import uuid4

from open_webui import config
from open_webui.models.files import Files
from open_webui.models.models import ModelModel, Models
from open_webui.soev import identity, ingest, projection
from open_webui.soev.client import SoevApiError, SoevClient

log = logging.getLogger(__name__)

ASSISTANT_TAG = 'assistant-files'
# A file still ingesting elsewhere (an editor upload lands in the owner's chat attachments first) is retried this
# often, this many times: ten minutes in all.
RETRY_SECONDS, RETRIES = 30, 20
_SYNCED = {'model.created', 'model.updated', 'model.access_updated', 'model.imported'}

_locks: dict[str, asyncio.Lock] = {}
_tasks: set[asyncio.Task] = set()


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
    grants = [grant.model_dump() for grant in model.access_grants]
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
    await identity.ensure_link(owner, client)
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
    try:
        collection = await client.get(_path(key), as_user=None)
    except SoevApiError as error:
        if error.status == 404:
            return
        raise
    await client.send(
        'DELETE',
        _path(key),
        None,
        as_user=collection['created_by'],
        idempotency_key=f'assistant-delete:{key}:{uuid4()}',
    )


async def sync(model_id: str, *, client: SoevClient | None = None) -> bool:
    """Bring the assistant's collection in step with the assistant: its access, its files submitted where they
    are missing and the ones it no longer has deleted. The collection of an assistant that is gone or has no files
    is deleted. True while a file still ingests elsewhere and waits for another sync."""
    client = client if client is not None else identity.build_client()
    model = await Models.get_model_by_id(model_id)
    ids = file_ids(model.meta.model_dump() if model is not None and model.meta else None)
    if not ids:
        await _delete_collection(model_id, client)
        return False
    key = await ensure_collection(model, client)
    documents = {document['source_id'] async for document in client.pages(_path(key) + '/documents', as_user=None)}
    owner = f'owui:user:{model.user_id}'
    for stale in sorted(documents - set(ids)):
        await client.send(
            'DELETE',
            f'{_path(key)}/documents/{quote(stale, safe="")}',
            None,
            as_user=owner,
            idempotency_key=f'assistant-file-delete:{key}:{stale}:{uuid4()}',
        )
    waiting = False
    for file in await Files.get_files_by_ids(ids):
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


async def _sync_serialized(model_id: str, retries: int) -> None:
    try:
        async with _locks.setdefault(model_id, asyncio.Lock()):
            waiting = await sync(model_id)
    except Exception as error:
        code = error.code if isinstance(error, SoevApiError) else type(error).__name__
        log.warning('Assistant files sync failed for %s (%s)', model_id, code)
        return
    if waiting and retries > 0:
        await asyncio.sleep(RETRY_SECONDS)
        await _sync_serialized(model_id, retries - 1)


def schedule(model_id: str) -> None:
    """Sync the assistant's collection in the background; syncs of one assistant run one at a time."""
    if not config.SOEV_API_URL:
        return
    task = asyncio.create_task(_sync_serialized(model_id, RETRIES))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


class AssistantFilesSink:
    """Syncs an assistant's collection when the assistant is saved, shared or deleted."""

    async def handle_event(self, app, event, request=None) -> None:
        subject = event.subject or {}
        if subject.get('type') != 'model' or not subject.get('id'):
            return
        if event.event in _SYNCED or event.event == 'model.deleted':
            schedule(str(subject['id']))


def install() -> None:
    """Register the sink with the event bus, once."""
    from open_webui.events import EVENT_SINKS

    if not any(isinstance(sink, AssistantFilesSink) for sink in EVENT_SINKS):
        EVENT_SINKS.append(AssistantFilesSink())
