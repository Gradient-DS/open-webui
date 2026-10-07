"""Ingest the loose chat uploads that stored chats still attach into their owners' attachments collections.

A v1 upload was read from its own vector collection; v2 reads it from the owner's attachments collection in
soev-api. Each file goes through ingest.submit as its owner, the path a fresh chat upload takes, and its
`collection_name` then names that collection. Knowledge base files are their KB's documents and images reach the
model as pixels, so neither is ingested here. A rerun skips files already ingested, in flight or terminally failed.
"""

import asyncio
from collections import Counter
from dataclasses import dataclass, field
from urllib.parse import quote

from open_webui.soev.client import SoevApiError

INGESTED, SUBMITTED, RUNNING, FAILED, PLANNED = 'ingested', 'submitted', 'running', 'failed', 'to check'
MISSING, KNOWLEDGE, GONE, REFERENCE, NO_OWNER = (
    'original missing',
    'knowledge base file',
    'no file row',
    'reference attachment',
    'owner missing',
)
CHAT_PAGE = 200
PROGRESS_EVERY = 50


@dataclass
class UploadState:
    counts: Counter = field(default_factory=Counter)
    bytes_planned: int = 0
    failures: list[str] = field(default_factory=list)
    failure_codes: Counter = field(default_factory=Counter)
    missing: list[str] = field(default_factory=list)

    def total(self, status: str) -> int:
        return self.counts[status]

    def fail(self, ref: str, code: str) -> None:
        """Ids and a stable code only: file names and soev-api details are content."""
        self.failures.append(f'{ref}: {code}')
        self.failure_codes[code] += 1


def _list(value) -> list:
    return value if isinstance(value, list) else []


def _entries(chat: dict):
    """Chat-level files and each message's, from the history tree and the legacy message list."""
    history = chat.get('history')
    tree = history.get('messages') if isinstance(history, dict) else None
    messages = [*(tree.values() if isinstance(tree, dict) else []), *_list(chat.get('messages'))]
    yield from _list(chat.get('files'))
    for message in messages:
        if isinstance(message, dict):
            yield from _list(message.get('files'))


def attached_file_ids(chat: dict) -> set[str]:
    """The uploads a chat attaches anywhere, as the agent would read them; not files picked from a KB."""
    from open_webui.soev.ingest import is_chat_attachment

    return {
        entry['id']
        for entry in _entries(chat or {})
        if isinstance(entry, dict) and is_chat_attachment(entry) and not entry.get('knowledge_id')
    }


async def _referenced(db) -> set[str]:
    import sqlalchemy as sa

    from open_webui.internal.db import get_async_db_context
    from open_webui.models.chats import Chat

    ids, after = set(), ''
    async with get_async_db_context(db) as session:
        while True:
            query = (
                sa.select(Chat.id, Chat.chat)
                .where(Chat.deleted_at.is_(None), Chat.id > after)
                .order_by(Chat.id)
                .limit(CHAT_PAGE)
            )
            page = (await session.execute(query)).all()
            if not page:
                return ids
            for _, chat in page:
                ids |= attached_file_ids(chat if isinstance(chat, dict) else {})
            after = page[-1][0]


async def _rows(ids: set[str], db) -> tuple[list, set[str], set[str]]:
    """The File rows, the ids among them linked to a KB, and the user ids that exist."""
    import sqlalchemy as sa

    from open_webui.internal.db import get_async_db_context
    from open_webui.models.files import File, FileModel
    from open_webui.models.knowledge import KnowledgeFile
    from open_webui.models.users import User

    files, linked = [], set()
    ordered = sorted(ids)
    async with get_async_db_context(db) as session:
        for start in range(0, len(ordered), CHAT_PAGE):
            batch = ordered[start : start + CHAT_PAGE]
            result = await session.execute(sa.select(File).where(File.id.in_(batch)))
            files.extend(FileModel.model_validate(row) for row in result.scalars().all())
            result = await session.execute(sa.select(KnowledgeFile.file_id).where(KnowledgeFile.file_id.in_(batch)))
            linked.update(result.scalars().all())
        owners = sorted({file.user_id for file in files})
        users = set((await session.execute(sa.select(User.id).where(User.id.in_(owners)))).scalars().all())
    return sorted(files, key=lambda file: file.id), linked, users


def _classify(file, key: str, documents: set[str]) -> str | None:
    meta = file.meta or {}
    if file.id in documents:
        return INGESTED
    if meta.get('soev_job') is not None:
        return RUNNING
    # The job poller keeps the collection key on failure, which marks a terminal result.
    if meta.get('status') == 'failed' and meta.get('soev_collection_key') == key:
        return FAILED
    return None


async def _point(file, key: str) -> None:
    from open_webui.models.files import Files

    if (file.meta or {}).get('collection_name') != key:
        await Files.update_file_metadata_by_id(file.id, {'collection_name': key})


class _Progress:
    def __init__(self, total: int, prefix: str) -> None:
        self.total, self.prefix, self.done = total, prefix, 0

    def step(self) -> None:
        self.done += 1
        if self.done % PROGRESS_EVERY == 0 or self.done == self.total:
            print(f'{self.prefix}: {self.done}/{self.total} submitted or checked', flush=True)


async def _submit(file, key: str, client, state: UploadState, gate: asyncio.Semaphore, progress: _Progress) -> None:
    from open_webui.soev import ingest

    async with gate:
        try:
            await ingest.submit(file, collection_key=key, user_id=file.user_id, client=client)
        except ingest.IngestBusy:
            state.counts[RUNNING] += 1
        except FileNotFoundError:
            state.counts[MISSING] += 1
            state.missing.append(f'{file.user_id}/{file.id}')
        except SoevApiError as error:
            # Server faults, throttling and auth problems are retried by the next run, not reported.
            if error.status >= 500 or error.status in {401, 403, 429}:
                raise
            state.counts[FAILED] += 1
            state.fail(f'{file.user_id}/{file.id}', error.code)
        except OSError as error:
            state.counts[FAILED] += 1
            state.fail(f'{file.user_id}/{file.id}', type(error).__name__)
        else:
            await _point(file, key)
            state.counts[SUBMITTED] += 1
        progress.step()


def _by_owner(files: list, linked: set[str], users: set[str], state: UploadState) -> dict[str, list]:
    owned: dict[str, list] = {}
    for file in files:
        if file.id in linked:
            state.counts[KNOWLEDGE] += 1
        elif (file.meta or {}).get('source'):
            # A v2 live-document attachment: soev-api reads it from its provider.
            state.counts[REFERENCE] += 1
        elif file.user_id not in users:
            state.counts[NO_OWNER] += 1
            state.fail(f'{file.user_id}/{file.id}', 'owner_missing')
        else:
            owned.setdefault(file.user_id, []).append(file)
    return owned


async def _check(owner: str, owned: list, client, state: UploadState, *, dry_run: bool) -> list:
    """Count the owner's files that need nothing; return the ones to submit."""
    from open_webui.soev import ingest
    from open_webui.soev.migrate_ingest import stored_code

    key = ingest.attachments_collection_key(owner)
    documents = set()
    if not dry_run:
        await ingest.ensure_attachments_collection(owner, client)
        path = f'/v1/collections/{quote(key, safe="")}/documents'
        documents = {document['source_id'] async for document in client.pages(path)}
    pending = []
    for file in owned:
        status = _classify(file, key, documents)
        if status is None and dry_run:
            state.counts[PLANNED] += 1
            state.bytes_planned += int((file.meta or {}).get('size') or 0)
        elif status is None:
            pending.append(file)
        elif status == FAILED:
            state.counts[status] += 1
            state.fail(f'{owner}/{file.id}', stored_code(file))
        else:
            state.counts[status] += 1
            if not dry_run:
                await _point(file, key)
    return pending


async def ingest_uploads(*, concurrency: int, dry_run: bool = False, db=None, prefix: str = '5 chat uploads'):
    """Put every upload a stored chat attaches into its owner's attachments collection; a rerun submits nothing
    twice. A dry run makes no HTTP request and writes nothing."""
    from open_webui.soev import identity, ingest

    state = UploadState()
    referenced = await _referenced(db)
    files, linked, users = await _rows(referenced, db)
    state.counts[GONE] = len(referenced) - len(files)
    print(f'{prefix}: {len(referenced)} files attached in chats, {len(files)} with a file row', flush=True)
    client = None if dry_run else identity.build_client()
    pending = []
    for owner, owned in sorted(_by_owner(files, linked, users, state).items()):
        pending += await _check(owner, owned, client, state, dry_run=dry_run)
    if pending:
        gate = asyncio.Semaphore(concurrency)
        progress = _Progress(len(pending), prefix)
        await asyncio.gather(
            *(
                _submit(file, ingest.attachments_collection_key(file.user_id), client, state, gate, progress)
                for file in pending
            )
        )
    return state
