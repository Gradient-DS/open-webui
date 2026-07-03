"""TOPdesk Sync Provider implementation.

Wraps ``TopdeskSyncWorker`` behind the ``SyncProvider`` / ``TokenManager``
interfaces. TOPdesk has a single service-account auth mode — there is no
per-user OAuth — so the token manager reports a sentinel "token" whenever the
service credential is configured (mirrors ``ConfluenceTokenManager``'s basic
branch).
"""

import logging
from typing import Optional

from open_webui.services.sync.provider import SyncProvider, TokenManager
from open_webui.services.topdesk.auth import TOPDESK_AUTH_SENTINEL, service_auth_configured

log = logging.getLogger(__name__)


class TopdeskTokenManager(TokenManager):
    """Token manager for TOPdesk.

    There is no per-user token — the worker reads the global service credential
    directly — so this reports a sentinel "token" whenever the service
    credential is configured. Same trick as Confluence's basic-auth branch.
    """

    async def get_valid_access_token(self, user_id: str, knowledge_id: str) -> Optional[str]:
        return TOPDESK_AUTH_SENTINEL if await service_auth_configured() else None

    async def has_stored_token(self, user_id: str, knowledge_id: str) -> bool:
        return await service_auth_configured()

    async def delete_token(self, user_id: str, knowledge_id: str) -> bool:
        # No per-user token to delete; this no-ops harmlessly.
        return False


class TopdeskSyncProvider(SyncProvider):
    """Sync provider for TOPdesk Knowledge Base."""

    def __init__(self):
        self._token_manager = TopdeskTokenManager()

    def get_provider_type(self) -> str:
        return 'topdesk'

    def get_meta_key(self) -> str:
        return 'topdesk_sync'

    def get_token_manager(self) -> TokenManager:
        return self._token_manager

    def create_worker(
        self,
        knowledge_id,
        sources,
        access_token,
        user_id,
        app,
        token_provider=None,
        use_shared_loader=False,
    ):
        from open_webui.services.topdesk.sync_worker import TopdeskSyncWorker

        # TOPdesk always ingests in-pod: there is no loader-worker TOPdesk source
        # client (unlike OneDrive/Google Drive/Confluence). Force
        # use_shared_loader=False so discovery + body fetch + parse + embed +
        # ingest all run inside the OWUI pod regardless of the global
        # USE_SHARED_LOADER flag.
        return TopdeskSyncWorker(
            knowledge_id=knowledge_id,
            sources=sources,
            access_token=access_token,
            user_id=user_id,
            app=app,
            token_provider=token_provider,
            use_shared_loader=False,
        )
