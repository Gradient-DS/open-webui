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

log = logging.getLogger(__name__)

CLEANUP_INTERVAL_SECONDS = 60

_cleanup_task: Optional[asyncio.Task] = None


def start_cleanup_worker():
    """Start the background cleanup worker. Called from main.py lifespan."""
    global _cleanup_task
    if _cleanup_task is None or _cleanup_task.done():
        _cleanup_task = asyncio.create_task(_run_cleanup_loop())
        log.info('Deletion cleanup worker started (interval: %ds)', CLEANUP_INTERVAL_SECONDS)


def stop_cleanup_worker():
    """Stop the background cleanup worker. Called from main.py lifespan shutdown."""
    global _cleanup_task
    if _cleanup_task and not _cleanup_task.done():
        _cleanup_task.cancel()
        log.info('Deletion cleanup worker stopped')
    _cleanup_task = None


async def _run_cleanup_loop():
    """Main cleanup loop. Processes pending deletions immediately on startup, then periodically."""
    # Process immediately on startup (crash recovery)
    try:
        await _process_pending_deletions()
    except Exception:
        log.exception('Error in initial cleanup run')

    while True:
        try:
            await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
            await _process_pending_deletions()
        except asyncio.CancelledError:
            log.info('Cleanup worker cancelled')
            return
        except Exception:
            log.exception('Error in cleanup loop')


async def _process_pending_deletions():
    """Process pending chat deletions."""
    await _process_pending_chat_deletions()


async def _process_pending_chat_deletions():
    """Process chats marked for deletion."""
    from open_webui.models.chats import Chats
    from open_webui.models.tags import Tags
    from open_webui.services.deletion import DeletionService

    pending_chats = await Chats.get_pending_deletions(limit=100)
    if not pending_chats:
        return

    log.info('Processing %d pending chat deletions', len(pending_chats))

    # Collect all file IDs across all pending chats
    all_file_ids: list[str] = []

    for chat in pending_chats:
        try:
            # Collect file IDs from this chat
            chat_files = await Chats.get_files_by_chat_id(chat.id)
            all_file_ids.extend(cf.file_id for cf in chat_files)

            # Clean up orphaned tags
            if chat.meta and chat.meta.get('tags'):
                for tag_name in chat.meta.get('tags', []):
                    try:
                        if await Chats.count_chats_by_tag_name_and_user_id(tag_name, chat.user_id) == 0:
                            await Tags.delete_tag_by_name_and_user_id(tag_name, chat.user_id)
                    except Exception as e:
                        log.warning('Failed to cleanup tag %s: %s', tag_name, e)

            # Hard-delete the chat (and its shared copy)
            await Chats.delete_chat_by_id(chat.id)

        except Exception:
            log.exception('Failed to cleanup chat %s', chat.id)

    # Batch cleanup orphaned files from all processed chats
    if all_file_ids:
        unique_file_ids = list(set(all_file_ids))
        file_report = await DeletionService.delete_orphaned_files_batch(unique_file_ids)
        if file_report.has_errors:
            log.warning('Chat file cleanup errors: %s', file_report.errors)
        log.info(
            'Chat file cleanup: %d storage, %d vectors, %d DB records',
            file_report.storage_files,
            file_report.vector_collections,
            file_report.total_db_records,
        )
