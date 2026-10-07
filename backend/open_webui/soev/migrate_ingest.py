"""Re-ingest local KB originals that have no soev document yet, a few at a time.

Cloud KBs re-sync through their recreated schedules instead. ingest.submit keys jobs by
content digest and refuses files with a job in flight, so a rerun submits nothing twice.
"""

import asyncio
import re
from collections import Counter
from dataclasses import dataclass, field
from urllib.parse import quote

from open_webui.soev.client import SoevApiError

CLOUD_TYPES = {'onedrive', 'google_drive', 'confluence'}
INGESTED, SUBMITTED, RUNNING, FAILED, PLANNED = 'ingested', 'submitted', 'running', 'failed', 'to check'
# The fixed reasons soev.jobs stores; anything else it stores is `<code>: <detail>` or a job status.
_POLLER_REASONS = {'job disappeared': 'job_not_found', 'upload could not be completed': 'upload_incomplete'}


def stored_code(file) -> str:
    """The stable code of a file's stored ingest error; never its detail, which can name the file."""
    error = str((file.meta or {}).get('error') or (file.data or {}).get('error') or '')
    if match := re.match(r'([a-z][a-z0-9_]*):', error):
        return match.group(1)
    if error in _POLLER_REASONS:
        return _POLLER_REASONS[error]
    if error.startswith('did not complete within'):
        return 'job_timed_out'
    if re.fullmatch(r'[A-Z_]+', error):
        return f'job_{error.lower()}'
    return 'ingest_failed'


@dataclass
class IngestState:
    per_kb: dict[str, Counter] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    failure_codes: Counter = field(default_factory=Counter)

    def count(self, key: str, status: str) -> None:
        self.per_kb.setdefault(key, Counter())[status] += 1

    def fail(self, ref: str, code: str) -> None:
        """Ids and a stable code only: file names, titles and soev-api details are content."""
        self.failures.append(f'{ref}: {code}')
        self.failure_codes[code] += 1

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
            state.fail(f'{key}/{file.id}', 'original_missing')
        except SoevApiError as error:
            # Server faults, throttling and auth problems are retried by the next run, not reported.
            if error.status >= 500 or error.status in {401, 403, 429}:
                raise
            state.count(key, FAILED)
            state.fail(f'{key}/{file.id}', error.code)
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
                state.fail(f'{kb.id}/{file.id}', 'kb_owner_missing')
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
                    state.fail(f'{kb.id}/{file.id}', stored_code(file))
            elif dry_run:
                state.count(kb.id, PLANNED)
            else:
                pending.append(_submit(file, kb.id, kb.user_id, directory.client, state, gate, ingest.submit))
        await asyncio.gather(*pending)
    return state
