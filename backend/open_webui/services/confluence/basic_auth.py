"""Confluence service-credential auth helpers.

Confluence supports three auth modes:

- ``oauth``   — per-user Atlassian 3LO (the default).
- ``basic``   — a single service credential (username + classic API token)
  talking directly to the configured site.
- ``scoped``  — a single service credential (email + Atlassian *scoped* API
  token) talking to the Atlassian gateway keyed by cloudId. Same Basic header
  as ``basic``; different transport (see ``confluence_client``).

``basic`` and ``scoped`` are the two *service modes*: a single shared service
credential (no per-user OAuth token), force the shared-KB flow, and the owner
is a service account. This module centralises the service-mode plumbing shared
by the provider, the sync worker and the router so none of them duplicate
config reads, the auth-mode resolution rule, or the basic-vs-scoped transport
choice.
"""

import logging
from typing import Optional, Dict, Any
from urllib.parse import urlparse

import httpx

from open_webui.config import (
    CONFLUENCE_AUTH_MODE,
    CONFLUENCE_SITE_URL,
    CONFLUENCE_BASIC_AUTH_USERNAME,
    CONFLUENCE_BASIC_AUTH_API_TOKEN,
    CONFLUENCE_SCOPED_API_TOKEN,
    CONFLUENCE_CLOUD_ID,
)
from open_webui.models.knowledge import Knowledges
from open_webui.services.confluence.confluence_client import ConfluenceClient

log = logging.getLogger(__name__)

# The two service modes: a single shared service credential rather than a
# per-user OAuth token. Both force the shared-KB flow and a service-account
# owner; only client construction + transport differ between them.
SERVICE_MODES = ('basic', 'scoped')


def is_service_mode(mode: str) -> bool:
    """True for the single-service-credential modes ('basic', 'scoped')."""
    return mode in SERVICE_MODES


# Non-empty placeholder returned where the sync pipeline expects an access
# token. In basic mode the worker reads the service credential straight from
# config, so the value itself is never used — it only has to be truthy so
# SyncProvider.execute_sync does not bail out with `needs_reauth`.
BASIC_AUTH_SENTINEL = '__confluence_basic_auth__'

_META_KEY = 'confluence_sync'


def global_auth_mode() -> str:
    """The configured global default auth mode ('oauth' | 'basic' | 'scoped')."""
    mode = CONFLUENCE_AUTH_MODE.value
    return mode if mode in ('oauth', 'basic', 'scoped') else 'oauth'


async def resolve_auth_mode(knowledge_id: Optional[str]) -> str:
    """Return the effective auth mode ('oauth' | 'basic' | 'scoped') for a KB.

    A KB stamps its auth_mode into ``confluence_sync`` meta when its first
    source is added, so the mode stays stable if the global setting later
    flips. Pseudo-KB ids used by the picker/general flows ('__picker__',
    '__general__') and None fall back to the global CONFLUENCE_AUTH_MODE.
    """
    if knowledge_id and not knowledge_id.startswith('__'):
        kb = await Knowledges.get_knowledge_by_id(knowledge_id)
        if kb:
            stamped = (kb.meta or {}).get(_META_KEY, {}).get('auth_mode')
            if stamped in ('oauth', 'basic', 'scoped'):
                return stamped
    return global_auth_mode()


def basic_auth_configured() -> bool:
    """True when all three basic-auth settings (site, username, token) are set."""
    return bool(
        (CONFLUENCE_SITE_URL.value or '').strip()
        and (CONFLUENCE_BASIC_AUTH_USERNAME.value or '').strip()
        and (CONFLUENCE_BASIC_AUTH_API_TOKEN.value or '').strip()
    )


def get_basic_site() -> Optional[Dict[str, Any]]:
    """The single Confluence site for basic auth, derived from CONFLUENCE_SITE_URL.

    Returns None when no site URL is configured. The site host doubles as the
    ``cloud_id`` so the rest of the sync pipeline — which keys sources by
    cloud_id — needs no special-casing for basic mode.
    """
    site_url = (CONFLUENCE_SITE_URL.value or '').strip().rstrip('/')
    if not site_url:
        return None
    host = urlparse(site_url).netloc or site_url
    return {'cloud_id': host, 'url': site_url, 'name': host}


def build_basic_client() -> ConfluenceClient:
    """Build a basic-mode ConfluenceClient from the global service credential."""
    return ConfluenceClient(
        auth_mode='basic',
        site_url=(CONFLUENCE_SITE_URL.value or '').strip(),
        basic_username=(CONFLUENCE_BASIC_AUTH_USERNAME.value or '').strip(),
        basic_api_token=(CONFLUENCE_BASIC_AUTH_API_TOKEN.value or ''),
    )


def basic_auth_credential() -> str:
    """The ``username:api_token`` pair shipped to the loader-worker.

    The loader-worker base64-encodes this into an ``Authorization: Basic``
    header. Returned as the raw pair (not encoded) — matching the loader-worker
    ConfluenceSourceClient's ``basic_auth`` contract.
    """
    username = (CONFLUENCE_BASIC_AUTH_USERNAME.value or '').strip()
    api_token = (CONFLUENCE_BASIC_AUTH_API_TOKEN.value or '').strip()
    return f'{username}:{api_token}'


# ----------------------------------------------------------------------------
# Scoped-mode plumbing
#
# A scoped token uses the same email:token Basic header as basic mode, but the
# token only works against the Atlassian gateway, which needs a real cloudId.
# The cloudId can be admin-supplied (CONFLUENCE_CLOUD_ID) or resolved from the
# site's unauthenticated `_edge/tenant_info` endpoint.
# ----------------------------------------------------------------------------


def scoped_auth_configured() -> bool:
    """True when site, service-account email, and scoped token are all set."""
    return bool(
        (CONFLUENCE_SITE_URL.value or '').strip()
        and (CONFLUENCE_BASIC_AUTH_USERNAME.value or '').strip()
        and (CONFLUENCE_SCOPED_API_TOKEN.value or '').strip()
    )


async def resolve_cloud_id(site_url: Optional[str] = None) -> Optional[str]:
    """Resolve the Atlassian cloudId for the scoped-token gateway path.

    Returns the manual override (``CONFLUENCE_CLOUD_ID``) when set; otherwise
    queries the site's unauthenticated ``_edge/tenant_info`` endpoint (de-facto
    but undocumented). Returns None when neither source yields a cloudId, so the
    caller can surface an actionable "set cloud id" error rather than firing a
    request at a malformed gateway URL.
    """
    override = (CONFLUENCE_CLOUD_ID.value or '').strip()
    if override:
        return override

    site = (site_url if site_url is not None else CONFLUENCE_SITE_URL.value or '').strip().rstrip('/')
    if not site:
        return None

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f'{site}/_edge/tenant_info')
            response.raise_for_status()
            cloud_id = (response.json() or {}).get('cloudId')
            return cloud_id or None
    except Exception as e:  # network error, non-2xx, malformed JSON — all non-fatal
        log.warning('Confluence cloudId resolution via %s/_edge/tenant_info failed: %s', site, e)
        return None


def build_scoped_client_for(cloud_id: str) -> ConfluenceClient:
    """Build a scoped-mode ConfluenceClient for an already-resolved cloudId.

    Synchronous: use where the cloudId is already known (e.g. the sync worker's
    per-source clients, which carry the resolved cloudId on every source), so no
    ``_edge/tenant_info`` round-trip is needed. :func:`build_scoped_client` is the
    async variant that resolves the cloudId first.
    """
    return ConfluenceClient(
        auth_mode='scoped',
        cloud_id=cloud_id or '',
        basic_username=(CONFLUENCE_BASIC_AUTH_USERNAME.value or '').strip(),
        basic_api_token=(CONFLUENCE_SCOPED_API_TOKEN.value or ''),
    )


async def build_scoped_client() -> ConfluenceClient:
    """Build a scoped-mode ConfluenceClient from the global service credential.

    The cloudId is resolved up-front (override or ``_edge/tenant_info``). When it
    cannot be resolved the client is built with an empty cloudId — callers should
    inspect ``client.cloud_id`` and report ``missing_cloud_id`` rather than fire a
    request at a malformed gateway URL.
    """
    cloud_id = await resolve_cloud_id()
    return build_scoped_client_for(cloud_id or '')


async def get_scoped_site() -> Optional[Dict[str, Any]]:
    """The single Confluence site for scoped auth.

    Unlike :func:`get_basic_site`, the ``cloud_id`` is the **real** Atlassian
    cloudId (resolved override or ``_edge/tenant_info``), since the scoped
    transport addresses the gateway by cloudId. Returns None when no site URL is
    configured; ``cloud_id`` may be None when it cannot be resolved.
    """
    site_url = (CONFLUENCE_SITE_URL.value or '').strip().rstrip('/')
    if not site_url:
        return None
    host = urlparse(site_url).netloc or site_url
    cloud_id = await resolve_cloud_id(site_url)
    return {'cloud_id': cloud_id, 'url': site_url, 'name': host}


def scoped_auth_credential() -> str:
    """The ``email:scoped_token`` pair shipped to the loader-worker.

    Same raw-pair contract as :func:`basic_auth_credential`; the loader-worker
    base64-encodes it into an ``Authorization: Basic`` header.
    """
    username = (CONFLUENCE_BASIC_AUTH_USERNAME.value or '').strip()
    api_token = (CONFLUENCE_SCOPED_API_TOKEN.value or '').strip()
    return f'{username}:{api_token}'


# ----------------------------------------------------------------------------
# Service-mode factories
#
# Dispatch by mode so callers that only care "single service credential vs
# per-user oauth" stay free of basic-vs-scoped branching. Both are async so the
# scoped path can resolve the cloudId; the basic path ignores the await cost.
# ----------------------------------------------------------------------------


def service_auth_configured(mode: str) -> bool:
    """True when the credential for the given service mode is fully configured."""
    if mode == 'scoped':
        return scoped_auth_configured()
    return basic_auth_configured()


async def build_service_client(mode: str) -> ConfluenceClient:
    """Build the ConfluenceClient for the given service mode ('basic'|'scoped')."""
    if mode == 'scoped':
        return await build_scoped_client()
    return build_basic_client()


async def service_site(mode: str) -> Optional[Dict[str, Any]]:
    """The single configured site for the given service mode ('basic'|'scoped')."""
    if mode == 'scoped':
        return await get_scoped_site()
    return get_basic_site()
