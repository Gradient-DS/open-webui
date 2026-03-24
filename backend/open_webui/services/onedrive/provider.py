"""
OneDrive Sync Provider implementation.

Wraps the OneDriveSyncWorker and token refresh service
behind the SyncProvider and TokenManager interfaces.
"""

import logging
from typing import Optional

from open_webui.services.sync.provider import SyncProvider, TokenManager
from open_webui.services.onedrive.token_refresh import (
    get_valid_access_token as _get_valid_access_token,
)
from open_webui.services.onedrive.auth import (
    get_stored_token,
    delete_stored_token,
)

log = logging.getLogger(__name__)


class OneDriveTokenManager(TokenManager):
    """Token manager for OneDrive using Microsoft OAuth v2.0."""

    async def get_valid_access_token(
        self, user_id: str, knowledge_id: str
    ) -> Optional[str]:
        return await _get_valid_access_token(user_id, knowledge_id)

    def has_stored_token(self, user_id: str, knowledge_id: str) -> bool:
        return get_stored_token(user_id) is not None

    def delete_token(self, user_id: str, knowledge_id: str) -> bool:
        return delete_stored_token(user_id)


class OneDriveSyncProvider(SyncProvider):
    """Sync provider for OneDrive / SharePoint."""

    def __init__(self):
        self._token_manager = OneDriveTokenManager()

    def get_provider_type(self) -> str:
        return "onedrive"

    def get_meta_key(self) -> str:
        return "onedrive_sync"

    def get_token_manager(self) -> TokenManager:
        return self._token_manager

    def create_worker(
        self, knowledge_id, sources, access_token, user_id, app, token_provider=None
    ):
        from open_webui.services.onedrive.sync_worker import OneDriveSyncWorker

        return OneDriveSyncWorker(
            knowledge_id=knowledge_id,
            sources=sources,
            access_token=access_token,
            user_id=user_id,
            app=app,
            token_provider=token_provider,
        )
