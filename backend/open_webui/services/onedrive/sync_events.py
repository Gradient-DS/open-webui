"""Socket.IO event emitter for OneDrive sync progress.

Delegates to the shared sync events module with the OneDrive event prefix.
"""

from typing import Optional, List, Dict, Any

from open_webui.services.sync.events import (
    emit_file_processing as _emit_file_processing,
    emit_file_added as _emit_file_added,
    emit_sync_progress as _emit_sync_progress,
)

_PREFIX = "onedrive"


async def emit_file_processing(
    user_id: str,
    knowledge_id: str,
    file_info: Dict[str, Any],
):
    """Emit event when a file starts processing during OneDrive sync."""
    await _emit_file_processing(_PREFIX, user_id, knowledge_id, file_info)


async def emit_file_added(
    user_id: str,
    knowledge_id: str,
    file_data: Dict[str, Any],
):
    """Emit event when a file is successfully added to the knowledge base."""
    await _emit_file_added(_PREFIX, user_id, knowledge_id, file_data)


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
    await _emit_sync_progress(
        _PREFIX,
        user_id,
        knowledge_id,
        status,
        current=current,
        total=total,
        filename=filename,
        error=error,
        files_processed=files_processed,
        files_failed=files_failed,
        deleted_count=deleted_count,
        failed_files=failed_files,
    )
