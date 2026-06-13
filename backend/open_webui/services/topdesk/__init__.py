"""TOPdesk Knowledge Base sync services.

Phase 2.3 ships the auth helpers and the GraphQL client. Later phases extend
this package (sync worker, provider, scheduler, sync events) and re-export them
here, mirroring ``services/confluence/__init__.py``.
"""

from open_webui.services.topdesk.topdesk_client import (
    TopdeskClient,
    TopdeskAuthError,
    TopdeskGraphQLError,
)
from open_webui.services.topdesk.auth import (
    TOPDESK_AUTH_SENTINEL,
    service_auth_configured,
    auth_headers,
    build_auth_header,
    get_service_site,
    build_client,
)

__all__ = [
    'TopdeskClient',
    'TopdeskAuthError',
    'TopdeskGraphQLError',
    'TOPDESK_AUTH_SENTINEL',
    'service_auth_configured',
    'auth_headers',
    'build_auth_header',
    'get_service_site',
    'build_client',
]
