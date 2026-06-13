"""TOPdesk service-account auth helpers.

TOPdesk has a single auth mode for sync: a service credential read from global
config (TOPDESK_URL + TOPDESK_USERNAME + TOPDESK_APP_PASSWORD). There is no
per-user OAuth flow — every sync against the configured tenant uses the same
application password (with an optional operator login). This module centralises
the config reads and auth-header construction shared by the provider, the sync
worker and the router so none of them duplicate the auth-mode rule.

Auth-header form depends on whether an operator login (username) is set
(findings §2.1):

- login set   → HTTP Basic, ``Authorization: Basic base64(login:app_password)``
  (operator login name + application password — the operator/Basic auth path).
- login empty → person-token form, ``Authorization: TOKEN id="<app_password>"``.

This mirrors ``services/confluence/basic_auth.py`` (sentinel + config helpers +
client builder), but TOPdesk has no oauth mode, so there is no auth-mode
resolution here.
"""

import logging
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from open_webui.config import (
    TOPDESK_URL,
    TOPDESK_USERNAME,
    TOPDESK_APP_PASSWORD,
)
from open_webui.services.topdesk.topdesk_client import TopdeskClient

log = logging.getLogger(__name__)

# Non-empty placeholder returned where the generic sync pipeline expects an
# access token. TOPdesk sync reads the service credential straight from config,
# so the value itself is never used — it only has to be truthy so
# SyncProvider.execute_sync does not bail out with `needs_reauth`. Same trick as
# Confluence's BASIC_AUTH_SENTINEL.
TOPDESK_AUTH_SENTINEL = '__topdesk_service_auth__'

_META_KEY = 'topdesk_sync'


def service_auth_configured() -> bool:
    """True when the TOPdesk service credential is usable.

    URL + app_password are required; the operator login is optional. An empty
    login selects the ``TOKEN id="..."`` person-token header form (findings §2.1)
    — a valid configuration — so it must NOT gate readiness.
    """
    return bool((TOPDESK_URL.value or '').strip() and (TOPDESK_APP_PASSWORD.value or '').strip())


def auth_headers() -> Dict[str, str]:
    """Build the Authorization header for the configured TOPdesk credential.

    - login set   → ``Authorization: Basic base64(login:app_password)``
    - login empty → ``Authorization: TOKEN id="<app_password>"``

    See findings §2.1. The header is static (the credential never refreshes), so
    callers may precompute it once.
    """
    return build_auth_header(
        username=(TOPDESK_USERNAME.value or '').strip(),
        app_password=(TOPDESK_APP_PASSWORD.value or ''),
    )


def build_auth_header(username: str, app_password: str) -> Dict[str, str]:
    """Pure helper: construct the Authorization header from raw credentials.

    Separated from ``auth_headers`` so the client and tests can build a header
    without touching global config.
    """
    import base64

    username = (username or '').strip()
    if username:
        encoded = base64.b64encode(f'{username}:{app_password}'.encode('utf-8')).decode('ascii')
        return {'Authorization': f'Basic {encoded}'}
    return {'Authorization': f'TOKEN id="{app_password}"'}


def get_service_site() -> Optional[Dict[str, Any]]:
    """The single TOPdesk tenant for the service credential, from TOPDESK_URL.

    Returns None when no URL is configured. The tenant host doubles as the
    ``cloud_id`` so the rest of the sync pipeline — which keys sources by
    cloud_id — needs no special-casing for TOPdesk. Mirrors Confluence's
    ``get_basic_site``.
    """
    url = (TOPDESK_URL.value or '').strip().rstrip('/')
    if not url:
        return None
    host = urlparse(url).netloc or url
    return {'cloud_id': host, 'url': url, 'name': host}


def build_client() -> TopdeskClient:
    """Build a TopdeskClient from the global service credential."""
    return TopdeskClient(
        base_url=(TOPDESK_URL.value or '').strip(),
        username=(TOPDESK_USERNAME.value or '').strip(),
        app_password=(TOPDESK_APP_PASSWORD.value or ''),
    )
