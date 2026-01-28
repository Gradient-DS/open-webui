"""
Retrieval Permission Filter

Provides file filtering for retrieval operations based on source permissions.
Import this module in retrieval.py to add permission filtering.
"""

import logging
from typing import List, Optional

from open_webui.services.permissions.enforcement import filter_accessible_files

log = logging.getLogger(__name__)


async def filter_retrieval_files(
    user_id: Optional[str],
    file_ids: List[str],
) -> List[str]:
    """
    Filter file IDs for retrieval based on source permissions.

    This is the single function to call from retrieval.py.
    Returns only files the user has source access to.
    """
    if not file_ids or not user_id:
        return file_ids

    try:
        return await filter_accessible_files(user_id, file_ids)
    except Exception as e:
        log.warning(f"Permission filter failed, returning original files: {e}")
        # Fail open to avoid breaking retrieval
        return file_ids
