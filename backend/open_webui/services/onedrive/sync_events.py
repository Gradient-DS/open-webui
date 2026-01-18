"""Socket.IO event emitter for OneDrive sync progress."""

import logging
from typing import Optional

log = logging.getLogger(__name__)


async def emit_sync_progress(
    user_id: str,
    knowledge_id: str,
    status: str,
    current: int = 0,
    total: int = 0,
    filename: str = "",
    error: Optional[str] = None,
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
            },
            room=f"user:{user_id}",
        )
        log.debug(
            f"Emitted sync progress: {status} {current}/{total} "
            f"for knowledge {knowledge_id}"
        )
    except Exception as e:
        log.debug(f"Failed to emit sync progress event: {e}")
