"""TOPdesk Knowledge Base sync services.

Ships the auth helpers, the REST KB client, the sync worker, the sync provider /
token manager, the background scheduler, and the Socket.IO sync-event forwarder,
mirroring ``services/confluence/__init__.py``.
"""

from open_webui.services.topdesk.topdesk_client import (
    TopdeskClient,
    TopdeskApiError,
    TopdeskAuthError,
    TopdeskTransientError,
)
from open_webui.services.topdesk.auth import (
    TOPDESK_AUTH_SENTINEL,
    service_auth_configured,
    auth_headers,
    build_auth_header,
    get_service_site,
    build_client,
)
from open_webui.services.topdesk.sync_worker import TopdeskSyncWorker
from open_webui.services.topdesk.provider import (
    TopdeskSyncProvider,
    TopdeskTokenManager,
)
from open_webui.services.topdesk.sync_events import emit_sync_progress
from open_webui.services.topdesk.scheduler import (
    start_scheduler,
    stop_scheduler,
)

__all__ = [
    'TopdeskClient',
    'TopdeskApiError',
    'TopdeskAuthError',
    'TopdeskTransientError',
    'TOPDESK_AUTH_SENTINEL',
    'service_auth_configured',
    'auth_headers',
    'build_auth_header',
    'get_service_site',
    'build_client',
    'TopdeskSyncWorker',
    'TopdeskSyncProvider',
    'TopdeskTokenManager',
    'emit_sync_progress',
    'start_scheduler',
    'stop_scheduler',
]
