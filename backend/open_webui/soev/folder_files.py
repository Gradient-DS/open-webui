"""[Gradient] A shared folder's own files, in a collection everyone the folder is shared with reads.

An uploaded file sits in its uploader's private attachments collection, which cannot be shared per document. So a
shared folder gets `owui-folder-<folder id>`, owned by the folder owner, read by the folder's audience
(`folder_sharing.audience`) and written by the owner only. It holds the folder's `file` entries the owner uploaded;
another person's file is theirs to share. It exists while the folder is shared and has such files. A turn reads a
folder file there first, as the user (`agent_v2._folder_attachment`).
"""

import asyncio
import logging
from urllib.parse import quote
from uuid import uuid4

from open_webui import config
from open_webui.models.files import Files
from open_webui.models.folders import Folders
from open_webui.models.users import Users
from open_webui.soev import identity, ingest, projection
from open_webui.soev.client import SoevApiError, SoevClient

log = logging.getLogger(__name__)

FOLDER_TAG = 'folder-files'
# A file still ingesting elsewhere is retried this often, this many times: ten minutes in all.
RETRY_SECONDS, RETRIES = 30, 20

_locks: dict[str, asyncio.Lock] = {}
_tasks: set[asyncio.Task] = set()


def file_ids(entries: list | None) -> list[str]:
    """The `file` entries of a folder's `data.files`, in order."""
    return list(
        dict.fromkeys(
            entry['id']
            for entry in entries or []
            if isinstance(entry, dict) and entry.get('type') == 'file' and isinstance(entry.get('id'), str)
        )
    )


def access(owner_id: str, principals: set[tuple[str, str]]) -> dict:
    """The collection's access: the folder's audience reads, the owner reads and writes."""
    grants = [
        {'principal_type': principal_type, 'principal_id': principal_id, 'permission': 'read'}
        for principal_type, principal_id in sorted(principals)
    ]
    visibility, readers, _ = projection.access_of(grants, service_principal=config.SOEV_API_SERVICE_PRINCIPAL)
    owner = f'owui:user:{owner_id}'
    return {'visibility': visibility, 'principals': sorted(set(readers) | {owner}), 'writers': [owner]}


def _path(key: str) -> str:
    return '/v1/collections/' + quote(key, safe='')


async def _existing(key: str, client: SoevClient) -> dict | None:
    try:
        return await client.get(_path(key), as_user=None)
    except SoevApiError as error:
        if error.status == 404:
            return None
        raise


async def _delete(collection: dict, client: SoevClient) -> None:
    key = collection['key']
    await client.send(
        'DELETE', _path(key), None, as_user=collection['created_by'], idempotency_key=f'folder-delete:{key}:{uuid4()}'
    )


async def _delete_documents(key: str, owner: str, stale: set[str], client: SoevClient) -> None:
    for source_id in sorted(stale):
        await client.send(
            'DELETE',
            f'{_path(key)}/documents/{quote(source_id, safe="")}',
            None,
            as_user=owner,
            idempotency_key=f'folder-file-delete:{key}:{source_id}:{uuid4()}',
        )


async def narrow(
    owner_id: str, folder_id: str, principals: set, files: list[str], *, client: SoevClient | None = None
) -> None:
    """Before a folder change commits: take away the readers and files the folder will no longer have, or the whole
    collection when the folder will not be shared or hold files any more. Never widens."""
    client = client if client is not None else identity.build_client()
    key = ingest.folder_collection_key(folder_id)
    collection = await _existing(key, client)
    if collection is None:
        return
    if not principals or not files:
        await _delete(collection, client)
        return
    owner = f'owui:user:{owner_id}'
    wanted = access(owner_id, principals)
    readers = sorted(set(collection['principals']) & (set(wanted['principals']) | {owner}))
    if readers != sorted(collection['principals']):
        body = {'visibility': 'restricted', 'principals': readers, 'writers': [owner]}
        await client.send('PUT', _path(key) + '/access', body, as_user=owner, idempotency_key=f'{key}:access:{uuid4()}')
    documents = {document['source_id'] async for document in client.pages(_path(key) + '/documents', as_user=None)}
    await _delete_documents(key, owner, documents - set(files), client)


async def sync(folder_id: str, *, client: SoevClient | None = None) -> bool:
    """Bring the folder's collection in step with the folder: its access and its owner's files, or no collection
    when it is gone, not shared or has no such files. True while a file still ingests elsewhere."""
    from open_webui.soev import folder_sharing

    client = client if client is not None else identity.build_client()
    key = ingest.folder_collection_key(folder_id)
    folder = await Folders.get_folder_by_id(folder_id)
    principals, files = set(), []
    if folder is not None:
        owner_user = await Users.get_user_by_id(folder.user_id)
        audience = folder_sharing.audience(await folder_sharing.view(folder.user_id), folder_id, folder.user_id)
        if owner_user is not None:
            principals = await folder_sharing.permitted(owner_user, 'knowledge', audience)
        ids = file_ids((folder.data or {}).get('files'))
        files = [file for file in await Files.get_files_by_ids(ids) if file.user_id == folder.user_id] if ids else []
    if not principals or not files:
        collection = await _existing(key, client)
        if collection is not None:
            await _delete(collection, client)
        return False
    owner = f'owui:user:{folder.user_id}'
    await identity.ensure_link(owner, client)
    body = access(folder.user_id, principals)
    if await _existing(key, client) is None:
        await client.send(
            'POST',
            '/v1/collections',
            {'key': key, 'name': 'Folder files', 'tags': [FOLDER_TAG], **body},
            as_user=owner,
            idempotency_key=f'kb:{key}:{uuid4()}',
        )
    else:
        await client.send('PUT', _path(key) + '/access', body, as_user=owner, idempotency_key=f'{key}:access:{uuid4()}')
    documents = {document['source_id'] async for document in client.pages(_path(key) + '/documents', as_user=None)}
    await _delete_documents(key, owner, documents - {file.id for file in files}, client)
    waiting = False
    for file in files:
        meta = file.meta or {}
        job = meta.get('soev_job')
        if job is not None:
            waiting = waiting or job.get('collection_key') != key
            continue
        if file.id in documents or (meta.get('status') == 'failed' and meta.get('soev_collection_key') == key):
            continue
        try:
            await ingest.submit(file, collection_key=key, user_id=folder.user_id, client=client)
        except ingest.IngestBusy:
            waiting = True
        except OSError as error:
            log.warning('Folder file %s of %s not submitted (%s)', file.id, folder_id, type(error).__name__)
    return waiting


async def _sync_serialized(folder_id: str, retries: int) -> None:
    try:
        async with _locks.setdefault(folder_id, asyncio.Lock()):
            waiting = await sync(folder_id)
    except Exception as error:
        code = error.code if isinstance(error, SoevApiError) else type(error).__name__
        log.warning('Folder files sync failed for %s (%s)', folder_id, code)
        return
    if waiting and retries > 0:
        await asyncio.sleep(RETRY_SECONDS)
        await _sync_serialized(folder_id, retries - 1)


def schedule(folder_id: str) -> None:
    """Sync the folder's collection in the background; syncs of one folder run one at a time."""
    if not config.SOEV_API_URL:
        return
    task = asyncio.create_task(_sync_serialized(folder_id, RETRIES))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
