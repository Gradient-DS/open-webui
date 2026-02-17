import time

import httpx

from open_webui.config import (
    EMAIL_GRAPH_TENANT_ID,
    EMAIL_GRAPH_CLIENT_ID,
    EMAIL_GRAPH_CLIENT_SECRET,
)

_token_cache: dict = {"access_token": None, "expires_at": 0}


async def get_mail_access_token(app) -> str:
    """Get Graph API token using client_credentials flow. Caches until expiry."""
    if _token_cache["access_token"] and time.time() < _token_cache["expires_at"] - 300:
        return _token_cache["access_token"]

    tenant_id = EMAIL_GRAPH_TENANT_ID
    client_id = EMAIL_GRAPH_CLIENT_ID
    client_secret = EMAIL_GRAPH_CLIENT_SECRET

    if not all([tenant_id, client_id, client_secret]):
        raise ValueError("Email Graph API credentials not configured")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
            data={
                "client_id": client_id,
                "scope": "https://graph.microsoft.com/.default",
                "client_secret": client_secret,
                "grant_type": "client_credentials",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    _token_cache["access_token"] = data["access_token"]
    _token_cache["expires_at"] = time.time() + data["expires_in"]
    return data["access_token"]
