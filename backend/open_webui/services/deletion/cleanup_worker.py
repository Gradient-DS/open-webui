"""
Periodic cleanup worker for soft-deleted knowledge bases and chats.

Architecture:
- Runs as an asyncio.Task started from main.py lifespan
- Processes pending deletions every CLEANUP_INTERVAL_SECONDS
- On startup, immediately processes any pending deletions (crash recovery)
- Uses existing DeletionService for the actual cleanup (idempotent, safe to retry)
"""

import asyncio
import logging
from typing import Optional
from starlette.concurrency import run_in_threadpool

log = logging.getLogger(__name__)

CLEANUP_INTERVAL_SECONDS = 60

_cleanup_task: Optional[asyncio.Task] = None


def start_cleanup_worker():
    """Start the background cleanup worker. Called from main.py lifespan."""
    global _cleanup_task
    if _cleanup_task is None or _cleanup_task.done():
        _cleanup_task = asyncio.create_task(_run_cleanup_loop())
        log.info("Deletion cleanup worker started (interval: %ds)", CLEANUP_INTERVAL_SECONDS)


def stop_cleanup_worker():
    """Stop the background cleanup worker. Called from main.py lifespan shutdown."""
    global _cleanup_task
    if _cleanup_task and not _cleanup_task.done():
        _cleanup_task.cancel()
        log.info("Deletion cleanup worker stopped")
    _cleanup_task = None


async def _run_cleanup_loop():
    """Main cleanup loop. Processes pending deletions immediately on startup, then periodically."""
    # Process immediately on startup (crash recovery)
    try:
        await run_in_threadpool(_process_pending_deletions)
    except Exception:
        log.exception("Error in initial cleanup run")

    while True:
        try:
            await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
            await run_in_threadpool(_process_pending_deletions)
        except asyncio.CancelledError:
            log.info("Cleanup worker cancelled")
            return
        except Exception:
            log.exception("Error in cleanup loop")


def _process_pending_deletions():
    """Process all pending KB and chat deletions. Runs in thread pool."""
    _process_pending_kb_deletions()
    _process_pending_chat_deletions()


def _process_pending_kb_deletions():
    """Process knowledge bases marked for deletion."""
    from open_webui.models.knowledge import Knowledges
    from open_webui.services.deletion import DeletionService

    pending_kbs = Knowledges.get_pending_deletions(limit=50)
    if not pending_kbs:
        return

    log.info("Processing %d pending KB deletions", len(pending_kbs))

    for kb in pending_kbs:
        try:
            # Collect file IDs before deletion (junction rows cascade on KB delete)
            kb_files = Knowledges.get_files_by_id(kb.id)
            kb_file_ids = [f.id for f in kb_files]

            # Full cascade: vector collection, model updates, hard-delete KB row
            report = DeletionService.delete_knowledge(kb.id, delete_files=False)

            if report.has_errors:
                log.warning("KB %s cleanup had errors: %s", kb.id, report.errors)

            # Clean up orphaned files (checks KB and chat references)
            if kb_file_ids:
                file_report = DeletionService.delete_orphaned_files_batch(kb_file_ids)
                if file_report.has_errors:
                    log.warning("KB %s file cleanup errors: %s", kb.id, file_report.errors)
                log.info(
                    "KB %s file cleanup: %d storage, %d vectors, %d DB records",
                    kb.id, file_report.storage_files,
                    file_report.vector_collections, file_report.total_db_records,
                )

            log.info("KB %s (%s) cleanup complete", kb.id, kb.name)

        except Exception:
            log.exception("Failed to cleanup KB %s", kb.id)


def _process_pending_chat_deletions():
    """Process chats marked for deletion."""
    from open_webui.models.chats import Chats
    from open_webui.models.tags import Tags
    from open_webui.services.deletion import DeletionService

    pending_chats = Chats.get_pending_deletions(limit=100)
    if not pending_chats:
        return

    log.info("Processing %d pending chat deletions", len(pending_chats))

    # Collect all file IDs across all pending chats
    all_file_ids: list[str] = []

    for chat in pending_chats:
        try:
            # Collect file IDs from this chat
            chat_files = Chats.get_files_by_chat_id(chat.id)
            all_file_ids.extend(cf.file_id for cf in chat_files)

            # Clean up orphaned tags
            if chat.meta and chat.meta.get("tags"):
                for tag_name in chat.meta.get("tags", []):
                    try:
                        if Chats.count_chats_by_tag_name_and_user_id(tag_name, chat.user_id) == 0:
                            Tags.delete_tag_by_name_and_user_id(tag_name, chat.user_id)
                    except Exception as e:
                        log.warning("Failed to cleanup tag %s: %s", tag_name, e)

            # Hard-delete the chat (and its shared copy)
            Chats.delete_chat_by_id(chat.id)

        except Exception:
            log.exception("Failed to cleanup chat %s", chat.id)

    # Batch cleanup orphaned files from all processed chats
    if all_file_ids:
        unique_file_ids = list(set(all_file_ids))
        file_report = DeletionService.delete_orphaned_files_batch(unique_file_ids)
        if file_report.has_errors:
            log.warning("Chat file cleanup errors: %s", file_report.errors)
        log.info(
            "Chat file cleanup: %d storage, %d vectors, %d DB records",
            file_report.storage_files,
            file_report.vector_collections, file_report.total_db_records,
        )
