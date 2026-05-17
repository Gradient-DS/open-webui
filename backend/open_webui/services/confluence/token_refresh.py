"""
Confluence Token Refresh Service.

Refreshes stored OAuth tokens using Atlassian's token endpoint.
Delegates shared token lifecycle to the generic token_refresh module.
"""

import time
import logging
from typing import Optional

import httpx

from open_webui.config import (
    CONFLUENCE_OAUTH_CLIENT_ID,
    CONFLUENCE_OAUTH_CLIENT_SECRET,
)
from open_webui.services.sync.token_refresh import (
    get_valid_access_token as _generic_get_valid_access_token,
)

log = logging.getLogger(__name__)

_TOKEN_URL = 'https://auth.atlassian.com/oauth/token'


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
        provider='confluence',
        meta_key='confluence_sync',
        user_id=user_id,
        knowledge_id=knowledge_id,
        refresh_fn=_refresh_token,
    )


async def _refresh_token(token_data: dict) -> Optional[dict]:
    """
    Refresh an OAuth token using the refresh_token grant.

    Atlassian rotates refresh tokens on each refresh — keep whatever we get.
    Preserves the previously-discovered `sites` list.

    Returns updated token dict on success, None on failure (revocation).
    """
    refresh_token = token_data.get('refresh_token')
    if not refresh_token:
        log.error('No refresh_token in stored Confluence token data')
        return None

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                _TOKEN_URL,
                json={
                    'grant_type': 'refresh_token',
                    'client_id': CONFLUENCE_OAUTH_CLIENT_ID.value,
                    'client_secret': CONFLUENCE_OAUTH_CLIENT_SECRET.value,
                    'refresh_token': refresh_token,
                },
                headers={'Content-Type': 'application/json'},
            )

            if response.status_code in (400, 401, 403):
                error_data = {}
                try:
                    error_data = response.json()
                except Exception:
                    pass
                error_code = error_data.get('error', '')
                if error_code in ('invalid_grant', 'unauthorized_client', 'invalid_client'):
                    log.warning('Confluence token revoked: %s', error_code)
                    return None
                log.error('Confluence token refresh error: %s %s', response.status_code, error_data)
                return None

            response.raise_for_status()
            new_token_data = response.json()

    except httpx.HTTPStatusError as e:
        log.error('Confluence token refresh HTTP error: %s', e.response.status_code)
        return None
    except Exception as e:
        log.error('Confluence token refresh error: %s', e)
        return None

    # Atlassian usually rotates the refresh token — preserve old one if new absent
    if 'refresh_token' not in new_token_data:
        new_token_data['refresh_token'] = refresh_token

    # Preserve discovered sites across refresh
    if 'sites' not in new_token_data and 'sites' in token_data:
        new_token_data['sites'] = token_data['sites']

    if 'expires_in' in new_token_data:
        new_token_data['expires_at'] = int(time.time()) + int(new_token_data['expires_in'])
    new_token_data['issued_at'] = int(time.time())

    return new_token_data
