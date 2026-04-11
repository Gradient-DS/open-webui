"""Generic OAuth token refresh logic for cloud sync providers.

Provides the shared token lifecycle flow (check expiry, refresh, mark needs_reauth).
Each provider supplies its own _refresh_token() implementation for the actual HTTP call.
"""

import time
import logging
from typing import Optional, Callable, Awaitable

from open_webui.models.oauth_sessions import OAuthSessions

log = logging.getLogger(__name__)

_REFRESH_BUFFER_SECONDS = 300  # 5 minutes


async def get_valid_access_token(
    provider: str,
    meta_key: str,
    user_id: str,
    knowledge_id: str,
    refresh_fn: Callable[[dict], Awaitable[Optional[dict]]],
) -> Optional[str]:
    """
    Get a valid access token, refreshing if needed.

    Args:
        provider: OAuth provider string (e.g. "google_drive", "onedrive")
        meta_key: Knowledge meta key (e.g. "google_drive_sync", "onedrive_sync")
        user_id: User ID
        knowledge_id: Knowledge base ID
        refresh_fn: Provider-specific async function to refresh a token dict.
                     Receives the current token_data dict, returns updated dict or None.
    """
    session = OAuthSessions.get_session_by_provider_and_user_id(provider, user_id)
    if not session:
        return None

    token_data = session.token
    expires_at = token_data.get('expires_at', 0)

    # Check if token needs refresh
    if time.time() + _REFRESH_BUFFER_SECONDS < expires_at:
        return token_data.get('access_token')

    # Token expired or near-expiry — refresh
    log.info('Refreshing token for user %s, KB %s', user_id, knowledge_id)
    new_token_data = await refresh_fn(token_data)

    if new_token_data is None:
        # Refresh failed — token likely revoked
        log.warning(
            'Token refresh failed for user %s, KB %s — marking as needs_reauth',
            user_id,
            knowledge_id,
        )
        _mark_needs_reauth(provider, meta_key, user_id)
        return None

    # Update stored token
    OAuthSessions.update_session_by_id(session.id, new_token_data)
    return new_token_data.get('access_token')


def _mark_needs_reauth(provider_type: str, meta_key: str, user_id: str):
    """Mark ALL knowledge bases of this provider type for a user as needing re-auth."""
    from open_webui.models.knowledge import Knowledges

    kbs = Knowledges.get_knowledge_bases_by_type(provider_type)
    for kb in kbs:
        if kb.user_id != user_id:
            continue
        meta = kb.meta or {}
        sync_info = meta.get(meta_key, {})
        sync_info['needs_reauth'] = True
        sync_info['has_stored_token'] = False
        meta[meta_key] = sync_info
        Knowledges.update_knowledge_meta_by_id(kb.id, meta)
