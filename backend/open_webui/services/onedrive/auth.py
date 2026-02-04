"""
OneDrive OAuth Auth Code Flow for Background Sync.

Uses confidential client (client_secret) with PKCE for obtaining
90-day refresh tokens. Tokens are stored encrypted in OAuthSessions.
"""

import hashlib
import base64
import secrets
import time
import logging
from typing import Optional, Dict, Any
from urllib.parse import urlencode

from open_webui.models.oauth_sessions import OAuthSessions
from open_webui.config import (
    ONEDRIVE_CLIENT_ID_BUSINESS,
    MICROSOFT_CLIENT_SECRET,
    ONEDRIVE_SHAREPOINT_TENANT_ID,
)

log = logging.getLogger(__name__)

# In-memory pending flows with TTL (10 minutes)
_pending_flows: Dict[str, Dict[str, Any]] = {}
_FLOW_TTL_SECONDS = 600

# Microsoft OAuth endpoints
_AUTHORITY_BASE = "https://login.microsoftonline.com"
_GRAPH_SCOPE = "https://graph.microsoft.com/Files.Read.All offline_access"


def _cleanup_expired_flows():
    """Remove expired pending flows."""
    now = time.time()
    expired = [k for k, v in _pending_flows.items() if now - v["created_at"] > _FLOW_TTL_SECONDS]
    for k in expired:
        del _pending_flows[k]


def _generate_pkce() -> tuple[str, str]:
    """Generate PKCE code_verifier and code_challenge (S256)."""
    code_verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


def get_authorization_url(
    user_id: str,
    knowledge_id: str,
    redirect_uri: str,
) -> str:
    """
    Build the Microsoft OAuth authorization URL.

    Returns the URL to redirect the user to for authorization.
    Stores the pending flow in memory for callback validation.
    """
    _cleanup_expired_flows()

    tenant_id = ONEDRIVE_SHAREPOINT_TENANT_ID.value or "common"
    code_verifier, code_challenge = _generate_pkce()
    state = secrets.token_urlsafe(32)

    _pending_flows[state] = {
        "user_id": user_id,
        "knowledge_id": knowledge_id,
        "code_verifier": code_verifier,
        "redirect_uri": redirect_uri,
        "created_at": time.time(),
    }

    params = {
        "client_id": ONEDRIVE_CLIENT_ID_BUSINESS,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": _GRAPH_SCOPE,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "response_mode": "query",
        "prompt": "consent",  # Force consent to ensure refresh token
    }

    return f"{_AUTHORITY_BASE}/{tenant_id}/oauth2/v2.0/authorize?{urlencode(params)}"


async def exchange_code_for_tokens(
    code: str,
    state: str,
    user_id: str,
) -> Dict[str, Any]:
    """
    Exchange authorization code for tokens and store in OAuthSessions.

    Validates the state parameter against pending flows,
    exchanges the code with Microsoft's token endpoint,
    and stores the encrypted token data.

    Returns dict with 'success', 'knowledge_id', and optionally 'error'.
    """
    import httpx

    _cleanup_expired_flows()

    # Validate state
    flow = _pending_flows.pop(state, None)
    if not flow:
        return {"success": False, "error": "Invalid or expired state parameter"}

    if flow["user_id"] != user_id:
        log.warning(
            "OAuth callback user mismatch: expected %s, got %s",
            flow["user_id"], user_id,
        )
        return {"success": False, "error": "User mismatch"}

    # Check TTL
    if time.time() - flow["created_at"] > _FLOW_TTL_SECONDS:
        return {"success": False, "error": "Authorization flow expired"}

    tenant_id = ONEDRIVE_SHAREPOINT_TENANT_ID.value or "common"
    token_url = f"{_AUTHORITY_BASE}/{tenant_id}/oauth2/v2.0/token"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                token_url,
                data={
                    "client_id": ONEDRIVE_CLIENT_ID_BUSINESS,
                    "client_secret": MICROSOFT_CLIENT_SECRET.value,
                    "code": code,
                    "redirect_uri": flow["redirect_uri"],
                    "grant_type": "authorization_code",
                    "code_verifier": flow["code_verifier"],
                },
            )
            response.raise_for_status()
            token_data = response.json()
    except httpx.HTTPStatusError as e:
        error_body = e.response.json() if e.response.headers.get("content-type", "").startswith("application/json") else {}
        log.error("Token exchange failed: %s %s", e.response.status_code, error_body.get("error_description", ""))
        return {"success": False, "error": error_body.get("error_description", "Token exchange failed")}
    except Exception as e:
        log.error("Token exchange error: %s", e)
        return {"success": False, "error": "Token exchange failed"}

    # Calculate expires_at from expires_in
    if "expires_in" in token_data and "expires_at" not in token_data:
        token_data["expires_at"] = int(time.time()) + int(token_data["expires_in"])
    token_data["issued_at"] = int(time.time())

    # Store in OAuthSessions with per-KB provider key
    knowledge_id = flow["knowledge_id"]
    provider = f"onedrive:{knowledge_id}"

    # Delete any existing session for this KB
    existing = OAuthSessions.get_session_by_provider_and_user_id(provider, user_id)
    if existing:
        OAuthSessions.delete_session_by_id(existing.id)

    session = OAuthSessions.create_session(
        user_id=user_id,
        provider=provider,
        token=token_data,
    )

    if not session:
        return {"success": False, "error": "Failed to store token"}

    log.info("Stored OAuth token for user %s, KB %s", user_id, knowledge_id)
    return {"success": True, "knowledge_id": knowledge_id}


def get_stored_token(user_id: str, knowledge_id: str) -> Optional[Dict[str, Any]]:
    """Get the stored token data for a knowledge base, or None."""
    provider = f"onedrive:{knowledge_id}"
    session = OAuthSessions.get_session_by_provider_and_user_id(provider, user_id)
    if not session:
        return None
    return session.token


def delete_stored_token(user_id: str, knowledge_id: str) -> bool:
    """Delete the stored token for a knowledge base."""
    provider = f"onedrive:{knowledge_id}"
    session = OAuthSessions.get_session_by_provider_and_user_id(provider, user_id)
    if not session:
        return False
    return OAuthSessions.delete_session_by_id(session.id)
