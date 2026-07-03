"""Generic OAuth token refresh logic for cloud sync providers.

Provides the shared token lifecycle flow (check expiry, refresh, mark needs_reauth).
Each provider supplies its own _refresh_token() implementation for the actual HTTP call.
"""

import time
import logging
from typing import Optional, Callable, Awaitable

from open_webui.models.oauth_sessions import OAuthSessions
from open_webui.services.sync.events import emit_sync_progress

log = logging.getLogger(__name__)

_REFRESH_BUFFER_SECONDS = 300  # 5 minutes

# Maps provider_type → Socket.IO event prefix used by the frontend
# listeners ({prefix}:sync:progress). Mirrors the per-provider sync_events.py
# _PREFIX constants — kept here so token_refresh can emit a terminal progress
# event when reauth is required, without importing provider-specific modules.
_PROVIDER_EVENT_PREFIXES = {
    'onedrive': 'onedrive',
    'google_drive': 'googledrive',
    'confluence': 'confluence',
}


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
    session = await OAuthSessions.get_session_by_provider_and_user_id(provider, user_id)
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
        await _mark_needs_reauth(provider, meta_key, user_id)
        return None

    # Update stored token
    await OAuthSessions.update_session_by_id(session.id, new_token_data)
    return new_token_data.get('access_token')


async def _mark_needs_reauth(provider_type: str, meta_key: str, user_id: str):
    """Mark ALL knowledge bases of this provider type for a user as needing re-auth.

    Any KB currently mid-sync is transitioned to 'cancelled' with
    ``cancel_reason='needs_reauth'`` so it doesn't sit forever in 'syncing'
    after the worker bails out — and a final progress event is emitted so a
    user watching the knowledge list sees the warning appear without a reload.
    """
    from open_webui.models.knowledge import Knowledges

    event_prefix = _PROVIDER_EVENT_PREFIXES.get(provider_type)
    kbs = await Knowledges.get_knowledge_bases_by_type(provider_type)
    for kb in kbs:
        if kb.user_id != user_id:
            continue
        meta = kb.meta or {}
        sync_info = meta.get(meta_key, {})
        sync_info['needs_reauth'] = True
        sync_info['has_stored_token'] = False

        was_syncing = sync_info.get('status') == 'syncing'
        if was_syncing:
            sync_info['status'] = 'cancelled'
            sync_info['cancel_reason'] = 'needs_reauth'

        meta[meta_key] = sync_info
        await Knowledges.update_knowledge_meta_by_id(kb.id, meta)

        if was_syncing and event_prefix:
            try:
                await emit_sync_progress(
                    provider_prefix=event_prefix,
                    user_id=user_id,
                    knowledge_id=kb.id,
                    status='cancelled',
                    current=sync_info.get('progress_current', 0) or 0,
                    total=sync_info.get('progress_total', 0) or 0,
                    stage_counts=sync_info.get('stage_counts'),
                    needs_reauth=True,
                )
            except Exception as e:
                # Event emission is best-effort — DB state is the source of truth.
                log.debug('Failed to emit needs_reauth cancel event for KB %s: %s', kb.id, e)
