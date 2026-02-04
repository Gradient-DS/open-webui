"""
OneDrive Token Refresh Service.

Refreshes stored OAuth tokens using the Microsoft v2.0 token endpoint.
Handles rotating refresh tokens and revocation detection.
"""

import time
import logging
from typing import Optional

import httpx

from open_webui.models.oauth_sessions import OAuthSessions
from open_webui.config import (
    ONEDRIVE_CLIENT_ID_BUSINESS,
    MICROSOFT_CLIENT_SECRET,
    ONEDRIVE_SHAREPOINT_TENANT_ID,
)

log = logging.getLogger(__name__)

_AUTHORITY_BASE = "https://login.microsoftonline.com"
_GRAPH_SCOPE = "https://graph.microsoft.com/Files.Read.All offline_access"

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
    provider = f"onedrive:{knowledge_id}"
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
        _mark_needs_reauth(knowledge_id)
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

    tenant_id = ONEDRIVE_SHAREPOINT_TENANT_ID.value or "common"
    token_url = f"{_AUTHORITY_BASE}/{tenant_id}/oauth2/v2.0/token"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                token_url,
                data={
                    "client_id": ONEDRIVE_CLIENT_ID_BUSINESS,
                    "client_secret": MICROSOFT_CLIENT_SECRET.value,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                    "scope": _GRAPH_SCOPE,
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

    # Microsoft may rotate refresh tokens — preserve old one if new not provided
    if "refresh_token" not in new_token_data:
        new_token_data["refresh_token"] = refresh_token

    # Calculate expires_at
    if "expires_in" in new_token_data:
        new_token_data["expires_at"] = int(time.time()) + int(new_token_data["expires_in"])
    new_token_data["issued_at"] = int(time.time())

    return new_token_data


def _mark_needs_reauth(knowledge_id: str):
    """Mark a knowledge base as needing re-authorization."""
    from open_webui.models.knowledge import Knowledges

    knowledge = Knowledges.get_knowledge_by_id(id=knowledge_id)
    if not knowledge:
        return

    meta = knowledge.meta or {}
    sync_info = meta.get("onedrive_sync", {})
    sync_info["needs_reauth"] = True
    sync_info["has_stored_token"] = False
    meta["onedrive_sync"] = sync_info
    Knowledges.update_knowledge_meta_by_id(knowledge_id, meta)
