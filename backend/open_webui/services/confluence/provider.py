"""
Confluence Sync Provider implementation.

Wraps the ConfluenceSyncWorker and token refresh service
behind the SyncProvider and TokenManager interfaces.
"""

import logging
from typing import Optional

from open_webui.services.sync.provider import SyncProvider, TokenManager
from open_webui.services.confluence.token_refresh import (
    get_valid_access_token as _get_valid_access_token,
)
from open_webui.services.confluence.auth import (
    get_stored_token,
    delete_stored_token,
)
from open_webui.services.confluence.basic_auth import (
    BASIC_AUTH_SENTINEL,
    basic_auth_configured,
    resolve_auth_mode,
)

log = logging.getLogger(__name__)


class ConfluenceTokenManager(TokenManager):
    """Token manager for Confluence.

    In ``oauth`` mode it resolves per-user Atlassian 3LO tokens. In ``basic``
    mode there is no per-user token — the worker reads the global service
    credential directly — so it reports a sentinel "token" whenever the basic
    credential is configured.
    """

    async def get_valid_access_token(self, user_id: str, knowledge_id: str) -> Optional[str]:
        if resolve_auth_mode(knowledge_id) == 'basic':
            return BASIC_AUTH_SENTINEL if basic_auth_configured() else None
        return await _get_valid_access_token(user_id, knowledge_id)

    def has_stored_token(self, user_id: str, knowledge_id: str) -> bool:
        if resolve_auth_mode(knowledge_id) == 'basic':
            return basic_auth_configured()
        return get_stored_token(user_id) is not None

    def delete_token(self, user_id: str, knowledge_id: str) -> bool:
        # basic mode has no per-user token to delete; this no-ops harmlessly.
        return delete_stored_token(user_id)


class ConfluenceSyncProvider(SyncProvider):
    """Sync provider for Confluence Cloud."""

    def __init__(self):
        self._token_manager = ConfluenceTokenManager()

    def get_provider_type(self) -> str:
        return 'confluence'

    def get_meta_key(self) -> str:
        return 'confluence_sync'

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
        from open_webui.services.confluence.sync_worker import ConfluenceSyncWorker

        # Confluence offloads to the per-tenant loader-worker like OneDrive and
        # Google Drive when USE_SHARED_LOADER is enabled — the genai-utils
        # loader-worker ships a Confluence source client (sources/confluence.py)
        # supporting both user_oauth and basic_auth credentials. Discovery (page
        # enumeration, version-delta detection, label/ancestor enrichment)
        # always stays in-pod; only body fetch + parse + embed + ingest
        # offloads. With USE_SHARED_LOADER off it runs the legacy in-pod path.
        return ConfluenceSyncWorker(
            knowledge_id=knowledge_id,
            sources=sources,
            access_token=access_token,
            user_id=user_id,
            app=app,
            token_provider=token_provider,
            use_shared_loader=use_shared_loader,
        )
