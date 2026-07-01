"""Restart-safe reconciler for the distributed document pipeline (warren).

Success is handled by the /ingest callback (warren POSTs chunked_text → the
file is set 'completed'), which is stateless and survives a backend restart.
This sweep catches what a callback cannot — a warren job that FAILS or HANGS
never calls /ingest, leaving its file stuck 'processing'.

Each tick it reads files still 'processing' that carry a ``pipeline_job_id``,
asks pipeline-api for each job's status, and marks the file 'error' on failure
or after a generous wall-clock backstop. Crucially it keys off the real job
status (a healthy job is waited on for as long as it runs) and holds NO
in-memory job state — everything is re-derived from the DB each tick, so it
survives backend pod restarts. Mirrors the services/sync SyncScheduler shape.
"""

import asyncio
import logging
import time

from open_webui.models.files import Files
from open_webui.services.files.events import emit_file_status
from open_webui.utils import doc_pipeline

log = logging.getLogger(__name__)


async def reconcile_pipeline_jobs(config, *, now: int) -> int:
    """Run one reconciliation pass. Returns the number of files marked 'error'."""
    base_url = config.PIPELINE_API_BASE_URL
    api_key = config.PIPELINE_API_KEY
    cap = config.PIPELINE_JOB_MAX_WALL_CLOCK_SECONDS

    files = await Files.get_processing_files_with_pipeline_job()
    errored = 0
    for file in files:
        meta = file.meta or {}
        job_id = meta.get('pipeline_job_id')
        if not job_id:
            continue
        submitted_at = meta.get('pipeline_submitted_at') or now
        try:
            job_status = await doc_pipeline.get_job_status(base_url=base_url, api_key=api_key, job_id=job_id)
        except Exception as e:
            # Transient (network blip, or job not yet visible) — don't fail the
            # file on it; the next tick retries.
            log.warning(f'doc-pipeline reconcile: status fetch failed for job {job_id} (file {file.id}): {e}')
            continue

        action = doc_pipeline.reconcile_action(
            job_status=job_status,
            age_seconds=now - submitted_at,
            max_wall_clock_seconds=cap,
        )
        if action == 'wait':
            continue

        # 'empty' is a *successful* parse that produced no chunks (e.g. a scanned
        # image-only PDF, or one where even OCR yields nothing). Keep it as a
        # completed KB member — matching the native zero-text path — but record a
        # warning in meta so the file list can flag it (and it can be
        # re-processed later) instead of hard-failing and vanishing. fail/timeout
        # stay hard errors below.
        if action == 'empty':
            warning = 'No searchable content could be extracted.'
            await Files.set_status(file.id, 'completed')
            await Files.update_file_metadata_by_id(file.id, {'warning': warning})
            await emit_file_status(user_id=file.user_id, file_id=file.id, status='completed', error=warning)
            log.info(f'doc-pipeline reconcile: marked file {file.id} completed-empty (job {job_id})')
            continue

        reason = (
            'distributed doc-pipeline job reported failure'
            if action == 'fail'
            else f'distributed doc-pipeline job did not complete within {cap}s'
        )
        await Files.set_status(file.id, 'error', error=reason)
        # Resolve the frontend's loading state: emit the honest 'failed' so the
        # spinner ends (toast + removal) instead of hanging until a page reload.
        # Naturally scoped to direct uploads — only files submitted via
        # route_file_to_pipeline / route_chat_file_to_pipeline carry a
        # pipeline_job_id, so get_processing_files_with_pipeline_job never
        # returns cloud-sync files.
        await emit_file_status(user_id=file.user_id, file_id=file.id, status='failed', error=reason)
        log.info(f'doc-pipeline reconcile: marked file {file.id} error ({action}, job {job_id})')
        errored += 1
    return errored


class PipelineReconciler:
    """Background asyncio task running reconcile_pipeline_jobs on an interval.

    Reads its interval + enable flag from ``app.state.config`` each tick, so an
    admin can toggle the feature or change cadence without a restart."""

    def __init__(self, app):
        self._app = app
        self._task = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while True:
            config = self._app.state.config
            interval = max(30, int(getattr(config, 'PIPELINE_RECONCILE_INTERVAL_SECONDS', 120)))
            await asyncio.sleep(interval)
            try:
                if not getattr(config, 'DISTRIBUTED_DOC_PIPELINE_ENABLED', False):
                    continue
                if not getattr(config, 'PIPELINE_API_BASE_URL', ''):
                    continue
                await reconcile_pipeline_jobs(config, now=int(time.time()))
            except asyncio.CancelledError:
                return
            except Exception as e:
                log.warning(f'doc-pipeline reconcile pass errored: {e}')


def start_pipeline_reconciler(app) -> PipelineReconciler:
    """Create + start the reconciler background task."""
    reconciler = PipelineReconciler(app)
    reconciler.start()
    return reconciler
