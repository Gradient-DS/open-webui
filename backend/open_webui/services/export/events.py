import logging
from typing import Optional

log = logging.getLogger(__name__)


async def emit_export_status(
    user_id: str,
    status: str,
    export_path: Optional[str] = None,
    error: Optional[str] = None,
):
    """
    Emit data export status via Socket.IO.

    Args:
        user_id: User who requested the export
        status: 'processing', 'completed', or 'failed'
        export_path: The download path if completed (relative to /cache/)
        error: Error message if status is 'failed'
    """
    try:
        from open_webui.socket.main import sio

        await sio.emit(
            'export:status',
            {
                'status': status,
                'export_path': export_path,
                'error': error,
            },
            room=f'user:{user_id}',
        )
    except Exception as e:
        log.debug(f'Failed to emit export status event: {e}')
