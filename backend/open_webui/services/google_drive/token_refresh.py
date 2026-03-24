"""
Google Drive Token Refresh Service.

Refreshes stored OAuth tokens using Google's token endpoint.
Delegates shared token lifecycle to the generic token_refresh module.
"""

import time
import logging
from typing import Optional

import httpx

from open_webui.config import (
    GOOGLE_DRIVE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
)
from open_webui.services.sync.token_refresh import (
    get_valid_access_token as _generic_get_valid_access_token,
)

log = logging.getLogger(__name__)

_TOKEN_URL = "https://oauth2.googleapis.com/token"


async def get_valid_access_token(
    user_id: str,
    knowledge_id: str,
) -> Optional[str]:
    """
    Get a valid access token for a knowledge base.

    If the stored token is expired or near-expiry, automatically refreshes it.
    Returns None if no token exists or refresh fails (token revoked).
    """
    return await _generic_get_valid_access_token(
        provider="google_drive",
        meta_key="google_drive_sync",
        user_id=user_id,
        knowledge_id=knowledge_id,
        refresh_fn=_refresh_token,
    )


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
