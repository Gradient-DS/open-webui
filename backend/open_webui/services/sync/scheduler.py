"""Generic background sync scheduler for cloud providers.

Periodically checks knowledge bases of a given provider type for sync eligibility
and executes syncs using stored OAuth refresh tokens.
"""

import asyncio
import time
import logging
from typing import Optional

from open_webui.models.knowledge import Knowledges, KnowledgeModel

log = logging.getLogger(__name__)


class SyncScheduler:
    """Background sync scheduler, parameterized by provider config.

    Architecture:
    - Runs as an asyncio.Task started from main.py lifespan
    - Receives the FastAPI app instance for sync worker mock requests
    - Syncs one KB at a time to limit resource usage
    - Skips KBs that are already syncing, need re-auth, or not due
    """

    def __init__(
        self,
        provider_type: str,
        meta_key: str,
        enable_config,
        interval_config,
    ):
        self.provider_type = provider_type
        self.meta_key = meta_key
        self.enable_config = enable_config
        self.interval_config = interval_config
        self._task: Optional[asyncio.Task] = None
        self._app = None

    def start(self, app):
        """Start the background sync scheduler.

        Called from main.py lifespan function. Stores the app reference
        and creates the scheduler asyncio task.
        """
        if not self.enable_config.value:
            log.info('%s sync disabled, scheduler not started', self.provider_type)
            return

        self._app = app
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())
            log.info('%s background sync scheduler started', self.provider_type)

    def stop(self):
        """Stop the background sync scheduler."""
        if self._task and not self._task.done():
            self._task.cancel()
            log.info('%s background sync scheduler stopped', self.provider_type)
        self._task = None

    async def _run(self):
        """Main scheduler loop."""
        interval_seconds = self.interval_config.value * 60

        # Wait one interval before first run (don't sync immediately on startup)
        await asyncio.sleep(interval_seconds)

        while True:
            try:
                await self._execute_due_syncs()
            except asyncio.CancelledError:
                log.info('Scheduler cancelled')
                return
            except Exception:
                log.exception('Error in scheduler loop')

            await asyncio.sleep(interval_seconds)

    async def _execute_due_syncs(self):
        """Find and execute syncs for all due knowledge bases."""
        from open_webui.services.sync.provider import get_sync_provider

        kbs = await Knowledges.get_knowledge_bases_by_type(self.provider_type)
        if not kbs:
            return

        now = time.time()
        interval_seconds = self.interval_config.value * 60
        provider = get_sync_provider(self.provider_type)

        for kb in kbs:
            if not await self._is_sync_due(kb, now, interval_seconds, provider):
                continue

            log.info('Starting scheduled sync for KB %s (%s)', kb.id, kb.name)

            try:
                await self._update_sync_status(kb.id, 'syncing')

                result = await provider.execute_sync(
                    knowledge_id=kb.id,
                    user_id=kb.user_id,
                    app=self._app,
                )

                if result.get('error'):
                    if result.get('needs_reauth'):
                        log.warning('KB %s needs re-authorization', kb.id)
                    else:
                        log.error(
                            'Scheduled sync failed for KB %s: %s',
                            kb.id,
                            result['error'],
                        )
                        await self._update_sync_status(kb.id, 'failed', error=result['error'])
                else:
                    log.info(
                        'Scheduled sync completed for KB %s: %d files processed',
                        kb.id,
                        result.get('files_processed', 0),
                    )

            except Exception:
                log.exception('Unexpected error during scheduled sync of KB %s', kb.id)
                await self._update_sync_status(kb.id, 'failed', error='Unexpected scheduler error')

    async def _is_sync_due(
        self,
        kb: KnowledgeModel,
        now: float,
        interval_seconds: float,
        sync_provider=None,
    ) -> bool:
        """Check if a knowledge base is due for scheduled sync."""
        meta = kb.meta or {}
        sync_info = meta.get(self.meta_key, {})

        # Skip if no sources configured
        if not sync_info.get('sources'):
            return False

        # Skip if no stored token (per-user DB lookup)
        if sync_provider and not await sync_provider.get_token_manager().has_stored_token(kb.user_id, kb.id):
            return False

        # Skip if needs re-authorization
        if sync_info.get('needs_reauth'):
            return False

        # Skip suspended KBs
        if sync_info.get('suspended_at'):
            return False

        # Skip if currently syncing (with staleness recovery)
        status = sync_info.get('status', 'idle')
        if status == 'syncing':
            sync_started = sync_info.get('sync_started_at')
            stale_threshold = 30 * 60  # 30 minutes
            is_stale = not sync_started or (now - sync_started) > stale_threshold
            if is_stale:
                log.warning(
                    'Stale sync detected for KB %s (started_at=%s), allowing re-sync',
                    kb.id,
                    sync_started,
                )
            else:
                return False

        # Check if enough time has passed since last sync
        last_sync = sync_info.get('last_sync_at', 0)
        return (now - last_sync) >= interval_seconds

    async def _update_sync_status(self, knowledge_id: str, status: str, error: str = None):
        """Update the sync status in knowledge meta."""
        knowledge = await Knowledges.get_knowledge_by_id(id=knowledge_id)
        if not knowledge:
            return

        meta = knowledge.meta or {}
        sync_info = meta.get(self.meta_key, {})
        sync_info['status'] = status
        if status == 'syncing':
            sync_info['sync_started_at'] = int(time.time())
        if error:
            sync_info['error'] = error
        meta[self.meta_key] = sync_info
        await Knowledges.update_knowledge_meta_by_id(knowledge_id, meta)
