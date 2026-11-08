"""Reconcile ingest jobs with their owner's current login token."""

import json
import logging
import re
import time
from contextlib import contextmanager
from urllib.parse import quote

from open_webui import config
from open_webui.models.files import FileModel, Files
from open_webui.services.files.events import emit_file_status
from open_webui.soev import acting, identity, ingest
from open_webui.soev.client import SoevApiError, SoevClient
from open_webui.soev.request_cache import batch_hook

log = logging.getLogger(__name__)

# An item's code and reason as soev-api gives them: fixed snake_case codes. A
# file's error is shown to its user, so anything else is dropped, never shown.
_FIXED_CODE = re.compile(r'[a-z][a-z0-9_]{0,63}')
_POLL_SECONDS = 5


def _item_error(item: dict, status: str) -> str:
    code, reason = item.get('code'), item.get('reason')
    if not isinstance(code, str) or not _FIXED_CODE.fullmatch(code):
        return status
    if isinstance(reason, str) and _FIXED_CODE.fullmatch(reason) and reason != code:
        return f'{code}: {reason}'
    return code


def _job_key(file: FileModel, job: dict) -> str:
    return f'ingest:{file.id}:{job["collection_key"]}:{job["sha256"]}:{job["attempt"]}'


async def _finish(file: FileModel, job: dict, error: str | None = None) -> int:
    status = 'failed' if error is not None else 'completed'
    if await Files.set_status(file.id, status, error=error) is None:
        raise RuntimeError('File status write failed')
    metadata = {'soev_job': None}
    if error is None:
        metadata['soev_collection_key'] = None
    if await Files.update_file_metadata_by_id(file.id, metadata) is None:
        raise RuntimeError('File metadata write failed')
    await emit_file_status(
        user_id=file.user_id,
        file_id=file.id,
        status=status,
        error=error,
        collection_name=job['collection_key'],
    )
    return 1


async def _commit_upload(client: SoevClient, file: FileModel, job: dict) -> int:
    try:
        await client.send(
            'POST',
            f'/v1/jobs/{quote(job["job_id"], safe="")}/commit',
            None,
            idempotency_key=f'{_job_key(file, job)}:commit',
        )
    except SoevApiError as error:
        if error.status != 409 or error.code != 'upload_missing':
            raise
        if job['attempt'] >= 3:
            return await _finish(file, job, 'upload could not be completed')
        await ingest.cancel(file, client=client)
        file = await Files.update_file_metadata_by_id(file.id, {'soev_collection_key': job['collection_key']})
        if file is None:
            raise RuntimeError('File metadata write failed')
        await ingest.submit(
            file,
            collection_key=job['collection_key'],
            user_id=file.user_id,
            attempt=job['attempt'] + 1,
            client=client,
        )
        return 1
    if await Files.update_file_metadata_by_id(file.id, {'soev_job': {**job, 'committed': True}}) is None:
        raise RuntimeError('File metadata write failed')
    return 1


async def _reference_ready(file: FileModel, job: dict, document: dict) -> int:
    if not file.meta.get('content_type'):
        ref = f'owui:user:{file.user_id}'
        if acting.requester_ref() != ref or not await acting.user_token(ref):
            return 0
        if document.get('content_type'):
            if await Files.update_file_metadata_by_id(file.id, {'content_type': document['content_type']}) is None:
                raise RuntimeError('File metadata write failed')
        else:
            return 0
    return await _finish(file, job)


async def _settle_file(
    client: SoevClient, file: FileModel, now: int, result: dict | None, document: dict | None
) -> int:
    job = file.meta['soev_job']
    if result is None:
        return await _finish(file, job, 'job disappeared')
    status = result['status']
    log.info(
        'soev job status: file=%s job=%s status=%s',
        file.id,
        job['job_id'],
        status
        if status
        in {
            'AWAITING_UPLOAD',
            'QUEUED',
            'RUNNING',
            'SUCCEEDED',
            'COMPLETED_WITH_ERRORS',
            'FAILED',
            'CANCELLED',
            'EXPIRED',
        }
        else 'UNKNOWN',
    )
    age = now - job['submitted_at']
    if status == 'AWAITING_UPLOAD':
        return await _commit_upload(client, file, job) if age >= 60 else 0
    if status in {'QUEUED', 'RUNNING'}:
        cap = config.SOEV_API_JOB_MAX_WALL_CLOCK_SECONDS
        return await _finish(file, job, f'did not complete within {cap}s') if age > cap else 0
    if status == 'SUCCEEDED':
        if document is None:
            return 0
        if job.get('kind') == 'reference':
            return await _reference_ready(file, job, document)
        path = f'/v1/collections/{quote(job["collection_key"], safe="")}/documents/{quote(file.id, safe="")}'
        if (document.get('path') or '') != (job['path'] or ''):
            await client.send(
                'POST',
                path + '/move',
                {'to': job['path'] or ''},
                as_user=f'owui:user:{file.user_id}',
                idempotency_key=f'{_job_key(file, job)}:move',
            )
        return await _finish(file, job)
    if status in {'COMPLETED_WITH_ERRORS', 'FAILED', 'CANCELLED', 'EXPIRED'}:
        item = next((item for item in result.get('items', []) if item.get('source_id') == file.id), {})
        return await _finish(file, job, _item_error(item, status))
    return 0


async def _claim_files(
    owner_id: str, now: int, *, max_collections: int = 15
) -> tuple[list[FileModel], dict[str, list[str]]]:
    candidates = sorted(
        await Files.get_files_with_soev_jobs(user_id=owner_id),
        key=lambda file: (file.meta.get('soev_job_polled_at', 0), file.id),
    )
    if not candidates:
        return [], {}
    anchor = min(candidates, key=lambda file: file.id)
    anchor = await Files.claim_soev_job_poll(
        anchor.id, owner_id, now=now, interval=max(_POLL_SECONDS, config.SOEV_API_JOB_POLL_SECONDS)
    )
    if anchor is None:
        return [], {}
    claimed = [anchor]
    collections = {anchor.meta['soev_job']['collection_key']: [anchor.id]}
    for file in candidates:
        if file.id == anchor.id:
            continue
        key = file.meta['soev_job']['collection_key']
        if len(claimed) >= 200 or (key not in collections and len(collections) >= max_collections):
            continue
        file = await Files.claim_soev_job_poll(
            file.id, owner_id, now=now, interval=max(_POLL_SECONDS, config.SOEV_API_JOB_POLL_SECONDS)
        )
        if file is not None:
            claimed.append(file)
            collections.setdefault(key, []).append(file.id)
    return claimed, collections


def _status_reads(claimed: list[FileModel], collections: dict[str, list[str]]) -> list[dict]:
    reads = [
        {
            'resource': 'jobs',
            'query': {
                'job_ids': list(dict.fromkeys(file.meta['soev_job']['job_id'] for file in claimed)),
                'include_items': True,
                'limit': 200,
            },
        }
    ]
    reads.extend(
        {
            'resource': 'documents',
            'collection_key': key,
            'query': {
                'source_ids': json.dumps(ids),
                'limit': 200,
            },
        }
        for key, ids in collections.items()
    )
    return reads


async def poll_once(client: SoevClient, *, now: int) -> int:
    ref = acting.requester_ref()
    if client.is_service or ref is None or not await acting.user_token(ref):
        return 0
    owner_id = ref.removeprefix('owui:user:')
    claimed, collections = await _claim_files(owner_id, now)
    if not claimed:
        return 0
    reads = _status_reads(claimed, collections)
    try:
        pages = await client.read_many(reads, as_user=ref)
        return await _settle_batch(client, claimed, collections, reads, pages, now)
    except Exception as error:
        code = error.code if isinstance(error, SoevApiError) else type(error).__name__
        log.warning('soev job batch failed (%s)', code)
        return 0


async def _settle_batch(
    client: SoevClient,
    claimed: list[FileModel],
    collections: dict[str, list[str]],
    reads: list[dict],
    pages: list[dict | None],
    now: int,
) -> int:
    try:
        if any(page is not None and not isinstance(page.get('data'), list) for page in pages):
            raise SoevApiError(502, 'upstream_error', 'Invalid status batch')
        statuses = {job['job_id']: job for job in (pages[0] or {}).get('data', [])}
        if set(statuses) - set(reads[0]['query']['job_ids']):
            raise SoevApiError(502, 'upstream_error', 'Unexpected job in status batch')
        documents = {
            (key, document['source_id']): document
            for key, page in zip(collections, pages[1:], strict=True)
            for document in (page or {}).get('data', [])
        }
    except Exception as error:
        code = error.code if isinstance(error, SoevApiError) else type(error).__name__
        log.warning('soev job batch failed (%s)', code)
        return 0
    touched = 0
    for file in claimed:
        try:
            job = file.meta['soev_job']
            touched += await _settle_file(
                client, file, now, statuses.get(job['job_id']), documents.get((job['collection_key'], file.id))
            )
        except Exception as error:
            code = error.code if isinstance(error, SoevApiError) else type(error).__name__
            log.warning('soev job poll failed for file %s (%s)', file.id, code)
    return touched


@contextmanager
def defer_owner_jobs(owner_id: str):
    used = False

    async def with_jobs(reads, fetch):
        nonlocal used
        if used or len(reads) > 14:
            return await fetch(reads)
        used = True
        ref = acting.requester_ref()
        if ref != f'owui:user:{owner_id}' or not await acting.user_token(ref):
            return await fetch(reads)
        now = int(time.time())
        claimed, collections = await _claim_files(owner_id, now, max_collections=15 - len(reads))
        if not claimed:
            return await fetch(reads)
        statuses = _status_reads(claimed, collections)
        results = await fetch(reads + statuses)
        try:
            pages = SoevClient.batch_values(results[len(reads) :])
            await _settle_batch(identity.build_client(), claimed, collections, statuses, pages, now)
        except Exception as error:
            code = error.code if isinstance(error, SoevApiError) else type(error).__name__
            log.warning('soev job batch failed (%s)', code)
        return results[: len(reads)]

    with batch_hook(with_jobs):
        yield


async def reconcile_owner_jobs(owner_id: str) -> int:
    if not config.SOEV_API_URL:
        return 0
    acting.require_owner(owner_id)
    return await poll_once(identity.build_client(), now=int(time.time()))
