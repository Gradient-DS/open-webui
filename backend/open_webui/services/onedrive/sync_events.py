"""Socket.IO event emitter for OneDrive sync progress."""

import logging
from typing import Optional, List, Dict

log = logging.getLogger(__name__)


async def emit_sync_progress(
    user_id: str,
    knowledge_id: str,
    status: str,
    current: int = 0,
    total: int = 0,
    filename: str = "",
    error: Optional[str] = None,
    files_processed: int = 0,
    files_failed: int = 0,
    deleted_count: int = 0,
    failed_files: Optional[List[Dict]] = None,
):
    """Emit sync progress event to a specific user via Socket.IO.

    Args:
        user_id: The user ID to send the event to
        knowledge_id: The knowledge base ID being synced
        status: Current sync status (syncing, completed, failed, etc.)
        current: Current file number being processed
        total: Total number of files to process
        filename: Name of the current file being processed
        error: Error message if status is 'failed'
        files_processed: Number of files successfully synced
        files_failed: Number of files that failed to sync
        deleted_count: Number of files deleted during sync
        failed_files: List of failed file details (filename, error_type, error_message)
    """
    try:
        from open_webui.socket.main import sio

        await sio.emit(
            "onedrive:sync:progress",
            {
                "knowledge_id": knowledge_id,
                "status": status,
                "current": current,
                "total": total,
                "filename": filename,
                "error": error,
                "files_processed": files_processed,
                "files_failed": files_failed,
                "deleted_count": deleted_count,
                "failed_files": failed_files,
            },
            room=f"user:{user_id}",
        )
        log.debug(
            f"Emitted sync progress: {status} {current}/{total} "
            f"for knowledge {knowledge_id}"
        )
    except Exception as e:
        log.debug(f"Failed to emit sync progress event: {e}")
