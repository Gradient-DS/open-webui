"""
Google Drive Sync Provider implementation.

Wraps the GoogleDriveSyncWorker and token refresh service
behind the SyncProvider and TokenManager interfaces.
"""

import logging
from typing import Optional, Dict, Any

from open_webui.services.sync.provider import SyncProvider, TokenManager
from open_webui.services.google_drive.token_refresh import (
    get_valid_access_token as _get_valid_access_token,
)
from open_webui.services.google_drive.auth import (
    get_stored_token,
    delete_stored_token,
)
from open_webui.models.knowledge import Knowledges

log = logging.getLogger(__name__)


class GoogleDriveTokenManager(TokenManager):
    """Token manager for Google Drive using Google OAuth 2.0."""

    async def get_valid_access_token(
        self, user_id: str, knowledge_id: str
    ) -> Optional[str]:
        return await _get_valid_access_token(user_id, knowledge_id)

    def has_stored_token(self, user_id: str, knowledge_id: str) -> bool:
        return get_stored_token(user_id) is not None

    def delete_token(self, user_id: str, knowledge_id: str) -> bool:
        return delete_stored_token(user_id)


class GoogleDriveSyncProvider(SyncProvider):
    """Sync provider for Google Drive."""

    def __init__(self):
        self._token_manager = GoogleDriveTokenManager()

    def get_provider_type(self) -> str:
        return "google_drive"

    def get_token_manager(self) -> TokenManager:
        return self._token_manager

    async def execute_sync(
        self,
        knowledge_id: str,
        user_id: str,
        app,
        access_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Execute Google Drive sync.

        If access_token is provided (manual sync), uses it directly.
        Otherwise, obtains a token from the token manager (background sync).
        """
        from open_webui.services.google_drive.sync_worker import GoogleDriveSyncWorker

        knowledge = Knowledges.get_knowledge_by_id(id=knowledge_id)
        if not knowledge:
            return {"error": "Knowledge base not found"}

        meta = knowledge.meta or {}
        sync_info = meta.get("google_drive_sync", {})
        sources = sync_info.get("sources", [])

        if not sources:
            return {"error": "No sync sources configured"}

        # Determine token source
        token_provider = None
        if access_token:
            effective_token = access_token
        else:
            effective_token = await self._token_manager.get_valid_access_token(
                user_id, knowledge_id
            )
            if not effective_token:
                return {"error": "No valid token available", "needs_reauth": True}

            async def _refresh():
                return await self._token_manager.get_valid_access_token(
                    user_id, knowledge_id
                )
            token_provider = _refresh

        worker = GoogleDriveSyncWorker(
            knowledge_id=knowledge_id,
            sources=sources,
            access_token=effective_token,
            user_id=user_id,
            app=app,
            token_provider=token_provider,
        )

        return await worker.sync()
