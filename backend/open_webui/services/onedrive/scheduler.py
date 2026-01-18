"""Background scheduler for OneDrive sync jobs.

Note: Scheduled sync requires storing and refreshing access tokens.
The current implementation using delegated tokens from the frontend
means tokens expire after ~1 hour and cannot be used for scheduled sync.

Options for future implementation:
1. Store refresh tokens securely and use them to get new access tokens
2. Use app-only authentication (requires different Azure AD permissions)
3. Implement a "keep token fresh" mechanism where users re-authenticate periodically

For now, this scheduler is a placeholder that can check which knowledge bases
are due for sync but will not execute the actual sync due to token limitations.
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional

from open_webui.models.knowledge import Knowledges
from open_webui.config import (
    ONEDRIVE_SYNC_INTERVAL_MINUTES,
    ENABLE_ONEDRIVE_SYNC,
)

log = logging.getLogger(__name__)

_scheduler_task: Optional[asyncio.Task] = None


async def run_scheduled_syncs():
    """Run scheduled sync checks for all configured Knowledge bases.

    Note: Due to token limitations, this currently only identifies which
    knowledge bases are due for sync. Actual sync requires user interaction
    to provide a fresh access token.
    """
    log.info("OneDrive sync scheduler started")

    while True:
        try:
            await _check_and_report_due_syncs()
        except Exception as e:
            log.exception(f"Scheduler error: {e}")

        # Wait for next interval
        interval_seconds = ONEDRIVE_SYNC_INTERVAL_MINUTES.value * 60
        log.debug(f"Scheduler sleeping for {interval_seconds} seconds")
        await asyncio.sleep(interval_seconds)


async def _check_and_report_due_syncs():
    """Check all knowledge bases and report those due for sync."""
    # Get all knowledge bases
    all_knowledge = Knowledges.get_knowledge_bases()

    current_time = int(datetime.utcnow().timestamp())
    interval_seconds = ONEDRIVE_SYNC_INTERVAL_MINUTES.value * 60

    due_for_sync = []

    for kb in all_knowledge:
        meta = kb.meta or {}
        sync_info = meta.get("onedrive_sync")

        if not sync_info:
            continue

        # Check if enough time has passed since last sync
        last_sync = sync_info.get("last_sync_at", 0)
        time_since_sync = current_time - last_sync

        if time_since_sync >= interval_seconds:
            due_for_sync.append({
                "id": kb.id,
                "name": kb.name,
                "last_sync": last_sync,
                "folder_path": sync_info.get("folder_path", ""),
            })

    if due_for_sync:
        log.info(
            f"Found {len(due_for_sync)} knowledge base(s) due for OneDrive sync: "
            f"{[kb['name'] for kb in due_for_sync]}"
        )
        # Note: Cannot execute sync without valid access token
        # This would require implementing token refresh mechanism
        log.debug(
            "Scheduled sync execution requires token refresh implementation. "
            "Users should manually trigger sync for now."
        )
    else:
        log.debug("No knowledge bases due for sync")


def start_scheduler():
    """Start the background scheduler.

    Should be called during app startup if ENABLE_ONEDRIVE_SYNC is True.
    """
    global _scheduler_task

    if not ENABLE_ONEDRIVE_SYNC.value:
        log.info("OneDrive sync is disabled, scheduler not started")
        return

    if _scheduler_task is None or _scheduler_task.done():
        _scheduler_task = asyncio.create_task(run_scheduled_syncs())
        log.info("OneDrive sync scheduler started")
    else:
        log.debug("OneDrive sync scheduler already running")


def stop_scheduler():
    """Stop the background scheduler.

    Should be called during app shutdown.
    """
    global _scheduler_task

    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
        log.info("OneDrive sync scheduler stopped")
    _scheduler_task = None


async def trigger_manual_sync_check():
    """Manually trigger a sync check (for admin dashboard).

    Returns list of knowledge bases that are due for sync.
    """
    all_knowledge = Knowledges.get_knowledge_bases()

    current_time = int(datetime.utcnow().timestamp())
    interval_seconds = ONEDRIVE_SYNC_INTERVAL_MINUTES.value * 60

    result = []

    for kb in all_knowledge:
        meta = kb.meta or {}
        sync_info = meta.get("onedrive_sync")

        if not sync_info:
            continue

        last_sync = sync_info.get("last_sync_at", 0)
        time_since_sync = current_time - last_sync
        is_due = time_since_sync >= interval_seconds

        result.append({
            "id": kb.id,
            "name": kb.name,
            "folder_path": sync_info.get("folder_path", ""),
            "last_sync_at": last_sync,
            "status": sync_info.get("status", "idle"),
            "is_due_for_sync": is_due,
            "next_sync_in_seconds": max(0, interval_seconds - time_since_sync),
        })

    return result
