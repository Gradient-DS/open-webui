"""Socket.IO event emitter for Google Drive sync progress."""

import logging
from typing import Optional, List, Dict, Any

log = logging.getLogger(__name__)


async def emit_file_processing(
    user_id: str,
    knowledge_id: str,
    file_info: Dict[str, Any],
):
    """Emit event when a file starts processing during Google Drive sync."""
    try:
        from open_webui.socket.main import sio

        await sio.emit(
            "googledrive:file:processing",
            {
                "knowledge_id": knowledge_id,
                "file": file_info,
            },
            room=f"user:{user_id}",
        )
        log.debug(
            f"Emitted file processing event for {file_info.get('name', 'unknown')} "
            f"in knowledge {knowledge_id}"
        )
    except Exception as e:
        log.debug(f"Failed to emit file processing event: {e}")


async def emit_file_added(
    user_id: str,
    knowledge_id: str,
    file_data: Dict[str, Any],
):
    """Emit event when a file is successfully added to the knowledge base."""
    try:
        from open_webui.socket.main import sio

        await sio.emit(
            "googledrive:file:added",
            {
                "knowledge_id": knowledge_id,
                "file": file_data,
            },
            room=f"user:{user_id}",
        )
        log.debug(
            f"Emitted file added event for {file_data.get('filename', 'unknown')} "
            f"to knowledge {knowledge_id}"
        )
    except Exception as e:
        log.debug(f"Failed to emit file added event: {e}")


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
    """Emit sync progress event to a specific user via Socket.IO."""
    try:
        from open_webui.socket.main import sio

        await sio.emit(
            "googledrive:sync:progress",
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
