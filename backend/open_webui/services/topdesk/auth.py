"""TOPdesk service-account auth helpers.

TOPdesk has a single auth mode for sync: a service credential read from global
config (TOPDESK_URL + TOPDESK_USERNAME + TOPDESK_APP_PASSWORD). There is no
per-user OAuth flow — every sync against the configured tenant uses the same
operator login + application password. This module centralises the config reads
and auth-header construction shared by the provider, the sync worker and the
router so none of them duplicate the auth rule.

Auth is HTTP **Basic** only (plan decision 4): the Knowledge Base REST API
(knowledge-base-v1) is operator-only and accepts only
``Authorization: Basic base64(login:app_password)`` — the operator login name
plus the application token. The legacy person-token (``TOKEN id="..."``) form was
removed because the REST KB API does not accept it, so the operator login is
required (not optional).

This mirrors ``services/confluence/basic_auth.py`` (sentinel + config helpers +
client builder), but TOPdesk has no oauth mode, so there is no auth-mode
resolution here.
"""

import logging
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from open_webui.models.config import Config
from open_webui.services.topdesk.topdesk_client import TopdeskClient

log = logging.getLogger(__name__)

# Non-empty placeholder returned where the generic sync pipeline expects an
# access token. TOPdesk sync reads the service credential straight from config,
# so the value itself is never used — it only has to be truthy so
# SyncProvider.execute_sync does not bail out with `needs_reauth`. Same trick as
# Confluence's BASIC_AUTH_SENTINEL.
TOPDESK_AUTH_SENTINEL = '__topdesk_service_auth__'

_META_KEY = 'topdesk_sync'


async def _service_credential() -> tuple[str, str, str]:
    """Read (url, username, app_password) live from the per-key Config store.

    Admin edits land in the Config store, not the import-time module vars, so
    these are re-read on every call (mirrors the scheduler fix) — an operator
    rotating the app password in the admin panel is picked up on the next sync
    without a pod restart.
    """
    values = await Config.get_many('topdesk.url', 'topdesk.username', 'topdesk.app_password')
    return (
        values.get('topdesk.url') or '',
        values.get('topdesk.username') or '',
        values.get('topdesk.app_password') or '',
    )


async def service_auth_configured() -> bool:
    """True when the TOPdesk service credential is usable.

    URL + operator login (username) + app_password are ALL required: the REST KB
    API is operator-Basic-only (plan decision 4), so an empty login can never
    authenticate and must gate readiness.
    """
    url, username, app_password = await _service_credential()
    return bool(url.strip() and username.strip() and app_password.strip())


async def auth_headers() -> Dict[str, str]:
    """Build the Authorization header for the configured TOPdesk credential.

    Always ``Authorization: Basic base64(login:app_password)`` (operator login +
    application token). The header is static (the credential never refreshes), so
    callers may precompute it once.
    """
    _url, username, app_password = await _service_credential()
    return build_auth_header(username=username.strip(), app_password=app_password)


def build_auth_header(username: str, app_password: str) -> Dict[str, str]:
    """Pure helper: construct the HTTP Basic Authorization header.

    Always emits ``Basic base64(username:app_password)`` (plan decision 4 — the
    REST KB API is operator-Basic-only; the legacy ``TOKEN id="..."`` form was
    dropped). Separated from ``auth_headers`` so the client and tests can build a
    header without touching global config.
    """
    import base64

    username = (username or '').strip()
    encoded = base64.b64encode(f'{username}:{app_password}'.encode('utf-8')).decode('ascii')
    return {'Authorization': f'Basic {encoded}'}


async def get_service_site() -> Optional[Dict[str, Any]]:
    """The single TOPdesk tenant for the service credential, from TOPDESK_URL.

    Returns None when no URL is configured. The tenant host doubles as the
    ``cloud_id`` so the rest of the sync pipeline — which keys sources by
    cloud_id — needs no special-casing for TOPdesk. Mirrors Confluence's
    ``get_basic_site``.
    """
    url = (await Config.get('topdesk.url', '') or '').strip().rstrip('/')
    if not url:
        return None
    host = urlparse(url).netloc or url
    return {'cloud_id': host, 'url': url, 'name': host}


async def build_client() -> TopdeskClient:
    """Build a TopdeskClient from the global service credential."""
    url, username, app_password = await _service_credential()
    return TopdeskClient(
        base_url=url.strip(),
        username=username.strip(),
        app_password=app_password,
    )
