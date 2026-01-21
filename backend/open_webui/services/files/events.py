import logging
from typing import Optional

log = logging.getLogger(__name__)


async def emit_file_status(
    user_id: str,
    file_id: str,
    status: str,
    error: Optional[str] = None,
    collection_name: Optional[str] = None,
):
    """
    Emit file processing status via Socket.IO.

    Args:
        user_id: User who owns the file
        file_id: The file ID
        status: 'completed' or 'failed'
        error: Error message if status is 'failed'
        collection_name: The vector collection name if completed
    """
    try:
        from open_webui.socket.main import sio

        await sio.emit(
            "file:status",
            {
                "file_id": file_id,
                "status": status,
                "error": error,
                "collection_name": collection_name,
            },
            room=f"user:{user_id}",
        )
    except Exception as e:
        log.debug(f"Failed to emit file status event: {e}")
