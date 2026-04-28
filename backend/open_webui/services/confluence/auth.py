"""
Confluence OAuth 3LO (Auth Code + PKCE) Flow for Background Sync.

Uses an Atlassian OAuth 2.0 confidential client with PKCE S256 to obtain
access + refresh tokens. After token exchange, calls accessible-resources
to discover the user's Confluence sites (cloudId + site url) and stores
them alongside the encrypted token payload in OAuthSessions.

Tokens are stored per-user (provider='confluence').
"""

import hashlib
import base64
import secrets
import time
import logging
from typing import Optional, Dict, Any, List
from urllib.parse import urlencode

import httpx

from open_webui.models.oauth_sessions import OAuthSessions
from open_webui.config import (
    CONFLUENCE_OAUTH_CLIENT_ID,
    CONFLUENCE_OAUTH_CLIENT_SECRET,
)

log = logging.getLogger(__name__)

# In-memory pending flows with TTL (10 minutes)
_pending_flows: Dict[str, Dict[str, Any]] = {}
_FLOW_TTL_SECONDS = 600

# Atlassian OAuth endpoints
_AUTH_URL = 'https://auth.atlassian.com/authorize'
_TOKEN_URL = 'https://auth.atlassian.com/oauth/token'
_ACCESSIBLE_RESOURCES_URL = 'https://api.atlassian.com/oauth/token/accessible-resources'

# Minimum scopes required to read spaces + pages and perform offline refresh.
# Uses Confluence *granular* scopes (required for Confluence REST API v2).
# See https://developer.atlassian.com/cloud/confluence/scopes-for-oauth-2-3LO-and-forge-apps/
_SCOPE = ' '.join(
    [
        # Minimum scopes for the REST API v2 endpoints we actually hit:
        #   /wiki/api/v2/spaces, /wiki/api/v2/spaces/{id}/pages,
        #   /wiki/api/v2/pages/{id}[/children]
        'read:page:confluence',
        'read:space:confluence',
        'read:hierarchical-content:confluence',
        'read:content-details:confluence',
        # offline_access is under the "Connect APIs" permission group
        'offline_access',
    ]
)

_AUDIENCE = 'api.atlassian.com'


def _cleanup_expired_flows():
    """Remove expired pending flows."""
    now = time.time()
    expired = [k for k, v in _pending_flows.items() if now - v['created_at'] > _FLOW_TTL_SECONDS]
    for k in expired:
        del _pending_flows[k]


def get_pending_flow(state: str) -> Optional[Dict[str, Any]]:
    """Get a pending flow by state parameter, or None if not found/expired."""
    _cleanup_expired_flows()
    return _pending_flows.get(state)


def remove_pending_flow(state: str) -> None:
    """Remove a pending flow by state parameter."""
    _pending_flows.pop(state, None)


def _generate_pkce() -> tuple[str, str]:
    """Generate PKCE code_verifier and code_challenge (S256)."""
    code_verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(code_verifier.encode('ascii')).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b'=').decode('ascii')
    return code_verifier, code_challenge


def get_authorization_url(
    user_id: str,
    knowledge_id: str,
    redirect_uri: str,
) -> str:
    """
    Build the Atlassian OAuth authorization URL.

    Returns the URL to redirect the user to for authorization.
    Stores the pending flow in memory for callback validation.
    """
    _cleanup_expired_flows()

    code_verifier, code_challenge = _generate_pkce()
    state = secrets.token_urlsafe(32)

    _pending_flows[state] = {
        'user_id': user_id,
        'knowledge_id': knowledge_id,
        'code_verifier': code_verifier,
        'redirect_uri': redirect_uri,
        'created_at': time.time(),
    }

    params = {
        'audience': _AUDIENCE,
        'client_id': CONFLUENCE_OAUTH_CLIENT_ID.value,
        'scope': _SCOPE,
        'redirect_uri': redirect_uri,
        'state': state,
        'response_type': 'code',
        'code_challenge': code_challenge,
        'code_challenge_method': 'S256',
        'prompt': 'consent',
    }

    return f'{_AUTH_URL}?{urlencode(params)}'


async def _fetch_accessible_resources(access_token: str) -> List[Dict[str, Any]]:
    """
    Discover the Confluence sites (cloudId + url) accessible to the token.

    Returns a list of {id, url, name, scopes, avatarUrl} dicts. Sites without
    a Confluence scope are filtered out.
    """
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                _ACCESSIBLE_RESOURCES_URL,
                headers={
                    'Authorization': f'Bearer {access_token}',
                    'Accept': 'application/json',
                },
            )
            response.raise_for_status()
            resources = response.json()
    except httpx.HTTPStatusError as e:
        log.error('accessible-resources fetch failed: %s', e.response.status_code)
        return []
    except Exception as e:
        log.error('accessible-resources fetch error: %s', e)
        return []

    confluence_sites = []
    for res in resources:
        scopes = res.get('scopes') or []
        # Accept classic ("read:confluence-...") AND granular
        # ("read:<thing>:confluence", "search:confluence") scope names.
        if any(
            s.startswith('read:confluence')
            or s.startswith('search:confluence')
            or s.endswith(':confluence')
            for s in scopes
        ):
            confluence_sites.append(res)

    return confluence_sites


async def exchange_code_for_tokens(
    code: str,
    state: str,
    user_id: str,
) -> Dict[str, Any]:
    """
    Exchange authorization code for tokens, discover Confluence sites,
    and store everything in OAuthSessions.

    Returns dict with 'success', 'knowledge_id', and optionally 'error'.
    """
    _cleanup_expired_flows()

    flow = _pending_flows.pop(state, None)
    if not flow:
        return {'success': False, 'error': 'Invalid or expired state parameter'}

    if flow['user_id'] != user_id:
        log.warning(
            'OAuth callback user mismatch: expected %s, got %s',
            flow['user_id'],
            user_id,
        )
        return {'success': False, 'error': 'User mismatch'}

    if time.time() - flow['created_at'] > _FLOW_TTL_SECONDS:
        return {'success': False, 'error': 'Authorization flow expired'}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                _TOKEN_URL,
                json={
                    'grant_type': 'authorization_code',
                    'client_id': CONFLUENCE_OAUTH_CLIENT_ID.value,
                    'client_secret': CONFLUENCE_OAUTH_CLIENT_SECRET.value,
                    'code': code,
                    'redirect_uri': flow['redirect_uri'],
                    'code_verifier': flow['code_verifier'],
                },
                headers={'Content-Type': 'application/json'},
            )
            response.raise_for_status()
            token_data = response.json()
    except httpx.HTTPStatusError as e:
        error_body = (
            e.response.json() if e.response.headers.get('content-type', '').startswith('application/json') else {}
        )
        log.error(
            'Token exchange failed: %s %s',
            e.response.status_code,
            error_body.get('error_description', ''),
        )
        return {
            'success': False,
            'error': error_body.get('error_description', 'Token exchange failed'),
        }
    except Exception as e:
        log.error('Token exchange error: %s', e)
        return {'success': False, 'error': 'Token exchange failed'}

    if 'expires_in' in token_data and 'expires_at' not in token_data:
        token_data['expires_at'] = int(time.time()) + int(token_data['expires_in'])
    token_data['issued_at'] = int(time.time())

    # Discover accessible Confluence sites using the fresh access token.
    sites = await _fetch_accessible_resources(token_data['access_token'])
    token_data['sites'] = [
        {
            'cloud_id': s.get('id'),
            'url': s.get('url'),
            'name': s.get('name') or s.get('url'),
            'scopes': s.get('scopes', []),
        }
        for s in sites
    ]

    if not token_data['sites']:
        return {
            'success': False,
            'error': 'No Confluence sites are accessible for this account',
        }

    knowledge_id = flow['knowledge_id']
    provider = 'confluence'

    existing = OAuthSessions.get_session_by_provider_and_user_id(provider, user_id)
    if existing:
        OAuthSessions.delete_session_by_id(existing.id)

    session = OAuthSessions.create_session(
        user_id=user_id,
        provider=provider,
        token=token_data,
    )

    if not session:
        return {'success': False, 'error': 'Failed to store token'}

    log.info(
        'Stored Confluence OAuth token for user %s, KB %s (%d site(s))',
        user_id,
        knowledge_id,
        len(token_data['sites']),
    )
    return {'success': True, 'knowledge_id': knowledge_id}


def get_stored_token(user_id: str) -> Optional[Dict[str, Any]]:
    """Get the stored Confluence token for a user, or None."""
    session = OAuthSessions.get_session_by_provider_and_user_id('confluence', user_id)
    if session:
        return session.token
    return None


def get_stored_sites(user_id: str) -> List[Dict[str, Any]]:
    """Return the list of accessible Confluence sites for a user."""
    token = get_stored_token(user_id)
    if not token:
        return []
    return token.get('sites', []) or []


def delete_stored_token(user_id: str) -> bool:
    """Delete the stored Confluence token for a user."""
    session = OAuthSessions.get_session_by_provider_and_user_id('confluence', user_id)
    if session:
        return OAuthSessions.delete_session_by_id(session.id)
    return False
