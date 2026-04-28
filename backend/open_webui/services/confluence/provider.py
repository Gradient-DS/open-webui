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

log = logging.getLogger(__name__)


class ConfluenceTokenManager(TokenManager):
    """Token manager for Confluence using Atlassian OAuth 3LO."""

    async def get_valid_access_token(self, user_id: str, knowledge_id: str) -> Optional[str]:
        return await _get_valid_access_token(user_id, knowledge_id)

    def has_stored_token(self, user_id: str, knowledge_id: str) -> bool:
        return get_stored_token(user_id) is not None

    def delete_token(self, user_id: str, knowledge_id: str) -> bool:
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

    def create_worker(self, knowledge_id, sources, access_token, user_id, app, token_provider=None):
        from open_webui.services.confluence.sync_worker import ConfluenceSyncWorker

        return ConfluenceSyncWorker(
            knowledge_id=knowledge_id,
            sources=sources,
            access_token=access_token,
            user_id=user_id,
            app=app,
            token_provider=token_provider,
        )
