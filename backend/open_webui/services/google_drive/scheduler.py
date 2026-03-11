"""
Google Drive Background Sync Scheduler.

Periodically checks Google Drive knowledge bases for sync eligibility
and executes syncs using stored OAuth refresh tokens.
"""

import asyncio
import time
import logging
from typing import Optional

from open_webui.config import (
    ENABLE_GOOGLE_DRIVE_SYNC,
    GOOGLE_DRIVE_SYNC_INTERVAL_MINUTES,
)
from open_webui.models.knowledge import Knowledges, KnowledgeModel

log = logging.getLogger(__name__)

_scheduler_task: Optional[asyncio.Task] = None
_app = None


def start_scheduler(app):
    """Start the background sync scheduler."""
    global _scheduler_task, _app

    if not ENABLE_GOOGLE_DRIVE_SYNC.value:
        log.info("Google Drive sync disabled, scheduler not started")
        return

    _app = app
    if _scheduler_task is None or _scheduler_task.done():
        _scheduler_task = asyncio.create_task(_run_scheduler())
        log.info("Google Drive background sync scheduler started")


def stop_scheduler():
    """Stop the background sync scheduler."""
    global _scheduler_task
    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
        log.info("Google Drive background sync scheduler stopped")
    _scheduler_task = None


async def _run_scheduler():
    """Main scheduler loop."""
    interval_seconds = GOOGLE_DRIVE_SYNC_INTERVAL_MINUTES.value * 60

    # Wait one interval before first run
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
    """Find and execute syncs for all due Google Drive knowledge bases."""
    from open_webui.services.sync.provider import get_sync_provider

    kbs = Knowledges.get_knowledge_bases_by_type("google_drive")
    if not kbs:
        return

    now = time.time()
    interval_seconds = GOOGLE_DRIVE_SYNC_INTERVAL_MINUTES.value * 60
    provider = get_sync_provider("google_drive")

    for kb in kbs:
        if not _is_sync_due(kb, now, interval_seconds, provider):
            continue

        log.info("Starting scheduled sync for KB %s (%s)", kb.id, kb.name)

        try:
            _update_sync_status(kb.id, "syncing")

            result = await provider.execute_sync(
                knowledge_id=kb.id,
                user_id=kb.user_id,
                app=_app,
            )

            if result.get("error"):
                if result.get("needs_reauth"):
                    log.warning("KB %s needs re-authorization", kb.id)
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


def _is_sync_due(kb: KnowledgeModel, now: float, interval_seconds: float, sync_provider=None) -> bool:
    """Check if a knowledge base is due for scheduled sync."""
    meta = kb.meta or {}
    sync_info = meta.get("google_drive_sync", {})

    if not sync_info.get("sources"):
        return False

    if sync_provider and not sync_provider.get_token_manager().has_stored_token(kb.user_id, kb.id):
        return False

    if sync_info.get("needs_reauth"):
        return False

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

    last_sync = sync_info.get("last_sync_at", 0)
    return (now - last_sync) >= interval_seconds


def _update_sync_status(knowledge_id: str, status: str, error: str = None):
    """Update the sync status in knowledge meta."""
    knowledge = Knowledges.get_knowledge_by_id(id=knowledge_id)
    if not knowledge:
        return

    meta = knowledge.meta or {}
    sync_info = meta.get("google_drive_sync", {})
    sync_info["status"] = status
    if status == "syncing":
        sync_info["sync_started_at"] = int(time.time())
    if error:
        sync_info["error"] = error
    meta["google_drive_sync"] = sync_info
    Knowledges.update_knowledge_meta_by_id(knowledge_id, meta)
