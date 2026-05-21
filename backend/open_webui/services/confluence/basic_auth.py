"""Confluence basic-auth (username + API token) helpers.

Confluence supports two auth modes:

- ``oauth``  — per-user Atlassian 3LO (the default).
- ``basic``  — a single service credential (username + API token) used for all
  Confluence sync against one configured site.

This module centralises the basic-mode plumbing shared by the provider, the
sync worker and the router so none of them duplicate config reads or the
auth-mode resolution rule.
"""

import logging
from typing import Optional, Dict, Any
from urllib.parse import urlparse

from open_webui.config import (
    CONFLUENCE_AUTH_MODE,
    CONFLUENCE_SITE_URL,
    CONFLUENCE_BASIC_AUTH_USERNAME,
    CONFLUENCE_BASIC_AUTH_API_TOKEN,
)
from open_webui.models.knowledge import Knowledges
from open_webui.services.confluence.confluence_client import ConfluenceClient

log = logging.getLogger(__name__)

# Non-empty placeholder returned where the sync pipeline expects an access
# token. In basic mode the worker reads the service credential straight from
# config, so the value itself is never used — it only has to be truthy so
# SyncProvider.execute_sync does not bail out with `needs_reauth`.
BASIC_AUTH_SENTINEL = '__confluence_basic_auth__'

_META_KEY = 'confluence_sync'


def global_auth_mode() -> str:
    """The configured global default auth mode ('oauth' | 'basic')."""
    mode = CONFLUENCE_AUTH_MODE.value
    return mode if mode in ('oauth', 'basic') else 'oauth'


def resolve_auth_mode(knowledge_id: Optional[str]) -> str:
    """Return the effective auth mode ('oauth' | 'basic') for a KB.

    A KB stamps its auth_mode into ``confluence_sync`` meta when its first
    source is added, so the mode stays stable if the global setting later
    flips. Pseudo-KB ids used by the picker/general flows ('__picker__',
    '__general__') and None fall back to the global CONFLUENCE_AUTH_MODE.
    """
    if knowledge_id and not knowledge_id.startswith('__'):
        kb = Knowledges.get_knowledge_by_id(knowledge_id)
        if kb:
            stamped = (kb.meta or {}).get(_META_KEY, {}).get('auth_mode')
            if stamped in ('oauth', 'basic'):
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
