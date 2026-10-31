"""Reconcile ingest jobs with their owner's current login token."""

import logging
import re
import time
from urllib.parse import quote

from open_webui import config
from open_webui.models.files import FileModel, Files
from open_webui.services.files.events import emit_file_status
from open_webui.soev import acting, identity, ingest
from open_webui.soev.client import SoevApiError, SoevClient

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


async def _reference_ready(client: SoevClient, file: FileModel, job: dict) -> int:
    if not file.meta.get('content_type'):
        path = f'/v1/collections/{quote(job["collection_key"], safe="")}/documents/{quote(file.id, safe="")}'
        ref = f'owui:user:{file.user_id}'
        if acting.requester_ref() != ref or not await acting.user_token(ref):
            return 0
        document = await client.get(path, as_user=ref)
        if document.get('content_type'):
            if await Files.update_file_metadata_by_id(file.id, {'content_type': document['content_type']}) is None:
                raise RuntimeError('File metadata write failed')
        else:
            return 0
    return await _finish(file, job)


async def _poll_file(client: SoevClient, file: FileModel, now: int) -> int:
    job = file.meta['soev_job']
    try:
        result = await client.get(
            f'/v1/jobs/{quote(job["job_id"], safe="")}',
            as_user=f'owui:user:{file.user_id}',
            params={'include_items': 'true'},
            cache=False,
        )
    except SoevApiError as error:
        if error.status == 404 and error.code == 'job_not_found':
            return await _finish(file, job, 'job disappeared')
        raise
    status = result['status']
    age = now - job['submitted_at']
    if status == 'AWAITING_UPLOAD':
        return await _commit_upload(client, file, job) if age >= 60 else 0
    if status in {'QUEUED', 'RUNNING'}:
        cap = config.SOEV_API_JOB_MAX_WALL_CLOCK_SECONDS
        return await _finish(file, job, f'did not complete within {cap}s') if age > cap else 0
    if status == 'SUCCEEDED':
        if job.get('kind') == 'reference':
            return await _reference_ready(client, file, job)
        path = f'/v1/collections/{quote(job["collection_key"], safe="")}/documents/{quote(file.id, safe="")}'
        document = await client.get(path, as_user=f'owui:user:{file.user_id}')
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


async def poll_once(client: SoevClient, *, now: int) -> int:
    ref = acting.requester_ref()
    if client.is_service or ref is None:
        return 0
    owner_id = ref.removeprefix('owui:user:')
    touched = 0
    for file in await Files.get_files_with_soev_jobs(user_id=owner_id):
        try:
            if not await acting.user_token(ref):
                return touched
            file = await Files.claim_soev_job_poll(
                file.id, owner_id, now=now, interval=max(_POLL_SECONDS, config.SOEV_API_JOB_POLL_SECONDS)
            )
            if file is not None:
                touched += await _poll_file(client, file, now)
        except Exception as error:
            code = error.code if isinstance(error, SoevApiError) else type(error).__name__
            log.warning('soev job poll failed for file %s (%s)', file.id, code)
    return touched


async def reconcile_owner_jobs(owner_id: str) -> int:
    if not config.SOEV_API_URL:
        return 0
    acting.require_owner(owner_id)
    return await poll_once(identity.build_client(), now=int(time.time()))
