"""
Google Drive Token Refresh Service.

Refreshes stored OAuth tokens using Google's token endpoint.
Handles refresh token rotation and revocation detection.
"""

import time
import logging
from typing import Optional

import httpx

from open_webui.models.oauth_sessions import OAuthSessions
from open_webui.config import (
    GOOGLE_DRIVE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
)

log = logging.getLogger(__name__)

_TOKEN_URL = "https://oauth2.googleapis.com/token"

# Refresh if token expires within this many seconds
_REFRESH_BUFFER_SECONDS = 300  # 5 minutes


async def get_valid_access_token(
    user_id: str,
    knowledge_id: str,
) -> Optional[str]:
    """
    Get a valid access token for a knowledge base.

    If the stored token is expired or near-expiry, automatically refreshes it.
    Returns None if no token exists or refresh fails (token revoked).
    """
    provider = "google_drive"
    session = OAuthSessions.get_session_by_provider_and_user_id(provider, user_id)
    if not session:
        return None

    token_data = session.token
    expires_at = token_data.get("expires_at", 0)

    # Check if token needs refresh
    if time.time() + _REFRESH_BUFFER_SECONDS < expires_at:
        return token_data.get("access_token")

    # Token expired or near-expiry — refresh
    log.info("Refreshing token for user %s, KB %s", user_id, knowledge_id)
    new_token_data = await _refresh_token(token_data)

    if new_token_data is None:
        # Refresh failed — token likely revoked
        log.warning("Token refresh failed for user %s, KB %s — marking as needs_reauth", user_id, knowledge_id)
        _mark_needs_reauth(user_id)
        return None

    # Update stored token
    OAuthSessions.update_session_by_id(session.id, new_token_data)
    return new_token_data.get("access_token")


async def _refresh_token(token_data: dict) -> Optional[dict]:
    """
    Refresh an OAuth token using the refresh_token grant.

    Returns updated token dict on success, None on failure (revocation).
    """
    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        log.error("No refresh_token in stored token data")
        return None

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                _TOKEN_URL,
                data={
                    "client_id": GOOGLE_DRIVE_CLIENT_ID.value,
                    "client_secret": GOOGLE_CLIENT_SECRET.value,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )

            if response.status_code == 400:
                error_data = response.json()
                error_code = error_data.get("error", "")
                if error_code in ("invalid_grant", "interaction_required"):
                    log.warning("Token revoked or requires interaction: %s", error_code)
                    return None
                log.error("Token refresh error: %s", error_data)
                return None

            response.raise_for_status()
            new_token_data = response.json()

    except httpx.HTTPStatusError as e:
        log.error("Token refresh HTTP error: %s", e.response.status_code)
        return None
    except Exception as e:
        log.error("Token refresh error: %s", e)
        return None

    # Google may not return a new refresh token — preserve the old one
    if "refresh_token" not in new_token_data:
        new_token_data["refresh_token"] = refresh_token

    # Calculate expires_at
    if "expires_in" in new_token_data:
        new_token_data["expires_at"] = int(time.time()) + int(new_token_data["expires_in"])
    new_token_data["issued_at"] = int(time.time())

    return new_token_data


def _mark_needs_reauth(user_id: str):
    """Mark ALL Google Drive knowledge bases for a user as needing re-authorization."""
    from open_webui.models.knowledge import Knowledges

    kbs = Knowledges.get_knowledge_bases_by_type("google_drive")
    for kb in kbs:
        if kb.user_id != user_id:
            continue
        meta = kb.meta or {}
        sync_info = meta.get("google_drive_sync", {})
        sync_info["needs_reauth"] = True
        sync_info["has_stored_token"] = False
        meta["google_drive_sync"] = sync_info
        Knowledges.update_knowledge_meta_by_id(kb.id, meta)
