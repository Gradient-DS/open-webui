"""
OneDrive Background Sync Scheduler.

Periodically checks OneDrive knowledge bases for sync eligibility
and executes syncs using stored OAuth refresh tokens.

Architecture:
- Runs as an asyncio.Task started from main.py lifespan
- Receives the FastAPI app instance for sync worker mock requests
- Syncs one KB at a time to limit resource usage
- Skips KBs that are already syncing, need re-auth, or not due
"""

import asyncio
import time
import logging
from typing import Optional

from open_webui.config import (
    ENABLE_ONEDRIVE_SYNC,
    ONEDRIVE_SYNC_INTERVAL_MINUTES,
)
from open_webui.models.knowledge import Knowledges, KnowledgeModel

log = logging.getLogger(__name__)

_scheduler_task: Optional[asyncio.Task] = None
_app = None  # FastAPI app instance, set during startup


def start_scheduler(app):
    """
    Start the background sync scheduler.

    Called from main.py lifespan function. Stores the app reference
    and creates the scheduler asyncio task.
    """
    global _scheduler_task, _app

    if not ENABLE_ONEDRIVE_SYNC.value:
        log.info("OneDrive sync disabled, scheduler not started")
        return

    _app = app
    if _scheduler_task is None or _scheduler_task.done():
        _scheduler_task = asyncio.create_task(_run_scheduler())
        log.info("OneDrive background sync scheduler started")


def stop_scheduler():
    """Stop the background sync scheduler."""
    global _scheduler_task
    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
        log.info("OneDrive background sync scheduler stopped")
    _scheduler_task = None


async def _run_scheduler():
    """Main scheduler loop."""
    interval_seconds = ONEDRIVE_SYNC_INTERVAL_MINUTES.value * 60

    # Wait one interval before first run (don't sync immediately on startup)
    await asyncio.sleep(interval_seconds)

    while True:
        try:
            await _execute_due_syncs()
        except asyncio.CancelledError:
            log.info("Scheduler cancelled")
            return
        except Exception:
            log.exception("Error in scheduler loop")

        await asyncio.sleep(interval_seconds)


async def _execute_due_syncs():
    """Find and execute syncs for all due OneDrive knowledge bases."""
    from open_webui.services.sync.provider import get_sync_provider

    kbs = Knowledges.get_knowledge_bases_by_type("onedrive")
    if not kbs:
        return

    now = time.time()
    interval_seconds = ONEDRIVE_SYNC_INTERVAL_MINUTES.value * 60
    provider = get_sync_provider("onedrive")

    for kb in kbs:
        if not _is_sync_due(kb, now, interval_seconds):
            continue

        log.info("Starting scheduled sync for KB %s (%s)", kb.id, kb.name)

        try:
            # Mark as syncing before starting
            _update_sync_status(kb.id, "syncing")

            result = await provider.execute_sync(
                knowledge_id=kb.id,
                user_id=kb.user_id,
                app=_app,
            )

            if result.get("error"):
                if result.get("needs_reauth"):
                    log.warning("KB %s needs re-authorization", kb.id)
                    # needs_reauth is already set by token_refresh._mark_needs_reauth
                else:
                    log.error("Scheduled sync failed for KB %s: %s", kb.id, result["error"])
                    _update_sync_status(kb.id, "failed", error=result["error"])
            else:
                log.info(
                    "Scheduled sync completed for KB %s: %d files processed",
                    kb.id, result.get("files_processed", 0),
                )

        except Exception:
            log.exception("Unexpected error during scheduled sync of KB %s", kb.id)
            _update_sync_status(kb.id, "failed", error="Unexpected scheduler error")


def _is_sync_due(kb: KnowledgeModel, now: float, interval_seconds: float) -> bool:
    """Check if a knowledge base is due for scheduled sync."""
    meta = kb.meta or {}
    sync_info = meta.get("onedrive_sync", {})

    # Skip if no sources configured
    if not sync_info.get("sources"):
        return False

    # Skip if no stored token
    if not sync_info.get("has_stored_token"):
        return False

    # Skip if needs re-authorization
    if sync_info.get("needs_reauth"):
        return False

    # Skip if currently syncing (with staleness recovery)
    status = sync_info.get("status", "idle")
    if status == "syncing":
        sync_started = sync_info.get("sync_started_at")
        stale_threshold = 30 * 60  # 30 minutes
        is_stale = not sync_started or (now - sync_started) > stale_threshold
        if is_stale:
            log.warning(
                "Stale sync detected for KB %s (started_at=%s), allowing re-sync",
                kb.id,
                sync_started,
            )
        else:
            return False

    # Check if enough time has passed since last sync
    last_sync = sync_info.get("last_sync_at", 0)
    return (now - last_sync) >= interval_seconds


def _update_sync_status(knowledge_id: str, status: str, error: str = None):
    """Update the sync status in knowledge meta."""
    knowledge = Knowledges.get_knowledge_by_id(id=knowledge_id)
    if not knowledge:
        return

    meta = knowledge.meta or {}
    sync_info = meta.get("onedrive_sync", {})
    sync_info["status"] = status
    if status == "syncing":
        sync_info["sync_started_at"] = int(time.time())
    if error:
        sync_info["error"] = error
    meta["onedrive_sync"] = sync_info
    Knowledges.update_knowledge_meta_by_id(knowledge_id, meta)
