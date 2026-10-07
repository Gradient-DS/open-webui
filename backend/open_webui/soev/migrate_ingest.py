"""Re-ingest local KB originals that have no soev document yet, a few at a time.

Cloud KBs re-sync through their recreated schedules instead. ingest.submit keys jobs by
content digest and refuses files with a job in flight, so a rerun submits nothing twice.
"""

import asyncio
from collections import Counter
from dataclasses import dataclass, field
from urllib.parse import quote

from open_webui.soev.client import SoevApiError

CLOUD_TYPES = {'onedrive', 'google_drive', 'confluence'}
INGESTED, SUBMITTED, RUNNING, FAILED, PLANNED = 'ingested', 'submitted', 'running', 'failed', 'to check'


@dataclass
class IngestState:
    per_kb: dict[str, Counter] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)

    def count(self, key: str, status: str) -> None:
        self.per_kb.setdefault(key, Counter())[status] += 1

    def total(self, status: str) -> int:
        return sum(counts[status] for counts in self.per_kb.values())


def _classify(file, key: str, documents: set[str]) -> str | None:
    meta = file.meta or {}
    if file.id in documents:
        return INGESTED
    if meta.get('soev_job') is not None:
        return RUNNING
    # The job poller keeps the collection key on failure, which marks a terminal result.
    status = meta.get('status') or (file.data or {}).get('status')
    if status == 'failed' and meta.get('soev_collection_key') == key:
        return FAILED
    return None


async def _submit(file, key: str, owner: str, client, state: IngestState, gate: asyncio.Semaphore, submit) -> None:
    from open_webui.soev.ingest import IngestBusy

    async with gate:
        try:
            await submit(file, collection_key=key, user_id=owner, client=client)
        except IngestBusy:
            state.count(key, RUNNING)
        except FileNotFoundError:
            state.count(key, FAILED)
            state.failures.append(f'{key}/{file.id}: original missing from storage')
        except SoevApiError as error:
            # Server faults, throttling and auth problems are retried by the next run, not reported.
            if error.status >= 500 or error.status in {401, 403, 429}:
                raise
            state.count(key, FAILED)
            state.failures.append(f'{key}/{file.id}: {error.code}')
        else:
            state.count(key, SUBMITTED)


async def reingest(directory, *, concurrency: int, dry_run: bool = False, db=None) -> IngestState:
    from open_webui.models.knowledge import KnowledgeTable
    from open_webui.soev import ingest

    table, state = KnowledgeTable(), IngestState()
    gate = asyncio.Semaphore(concurrency)
    for kb in directory.rows:
        if kb.type in CLOUD_TYPES or kb.id in directory.conflicts:
            continue
        files = await table.get_files_by_id(kb.id, db=db)
        state.per_kb[kb.id] = Counter()
        if kb.user_id not in directory.user_ids:
            for file in files:
                state.count(kb.id, FAILED)
                state.failures.append(f'{kb.id}/{file.id}: KB owner missing')
            continue
        documents = set()
        if not dry_run:
            path = f'/v1/collections/{quote(kb.id, safe="")}/documents'
            documents = {document['source_id'] async for document in directory.client.pages(path)}
        pending = []
        for file in sorted(files, key=lambda file: file.id):
            status = _classify(file, kb.id, documents)
            if status is not None:
                state.count(kb.id, status)
                if status == FAILED:
                    error = (file.meta or {}).get('error') or (file.data or {}).get('error') or 'ingest failed'
                    state.failures.append(f'{kb.id}/{file.id}: {error}')
            elif dry_run:
                state.count(kb.id, PLANNED)
            else:
                pending.append(_submit(file, kb.id, kb.user_id, directory.client, state, gate, ingest.submit))
        await asyncio.gather(*pending)
    return state
