"""
Sync Provider Abstraction Layer.

Defines interfaces for external datasource sync providers.
Follows the factory-singleton pattern used by StorageProvider and VectorDBBase.

To add a new datasource:
1. Create a new directory under services/ (e.g., services/dropbox/)
2. Subclass SyncProvider and TokenManager
3. Add a case to get_sync_provider() and get_token_manager()
4. Add the provider type to the Knowledge model's type validation
"""

import logging
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any

from open_webui.models.knowledge import Knowledges

log = logging.getLogger(__name__)


class TokenManager(ABC):
    """Manages OAuth token lifecycle for a sync provider."""

    @abstractmethod
    async def get_valid_access_token(self, user_id: str, knowledge_id: str) -> Optional[str]:
        """
        Get a valid access token, refreshing if needed.
        Returns None if no token exists or refresh failed.
        """
        ...

    @abstractmethod
    def has_stored_token(self, user_id: str, knowledge_id: str) -> bool:
        """Check if a stored token exists (may be expired)."""
        ...

    @abstractmethod
    def delete_token(self, user_id: str, knowledge_id: str) -> bool:
        """Delete stored token. Returns True if deleted."""
        ...


class SyncProvider(ABC):
    """Executes sync operations for an external datasource.

    Subclasses must implement:
    - get_provider_type() -> str
    - get_meta_key() -> str
    - get_token_manager() -> TokenManager
    - create_worker(knowledge_id, sources, access_token, user_id, app, token_provider) -> worker
    """

    @abstractmethod
    def get_provider_type(self) -> str:
        """Return the provider type string (e.g., 'onedrive', 'google_drive')."""
        ...

    @abstractmethod
    def get_meta_key(self) -> str:
        """Return the knowledge meta key (e.g., 'onedrive_sync', 'google_drive_sync')."""
        ...

    @abstractmethod
    def get_token_manager(self) -> TokenManager:
        """Return the token manager for this provider."""
        ...

    @abstractmethod
    def create_worker(
        self,
        knowledge_id: str,
        sources,
        access_token: str,
        user_id: str,
        app,
        token_provider=None,
    ):
        """Create the provider-specific sync worker instance."""
        ...

    async def execute_sync(
        self,
        knowledge_id: str,
        user_id: str,
        app,
        access_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Execute a sync for a knowledge base.

        If access_token is provided (manual sync), uses it directly.
        Otherwise, obtains a token from the token manager (background sync).
        """
        knowledge = Knowledges.get_knowledge_by_id(id=knowledge_id)
        if not knowledge:
            return {'error': 'Knowledge base not found'}

        meta = knowledge.meta or {}
        sync_info = meta.get(self.get_meta_key(), {})
        sources = sync_info.get('sources', [])

        if not sources:
            return {'error': 'No sync sources configured'}

        # Determine token source
        token_provider = None
        if access_token:
            effective_token = access_token
        else:
            effective_token = await self.get_token_manager().get_valid_access_token(user_id, knowledge_id)
            if not effective_token:
                return {'error': 'No valid token available', 'needs_reauth': True}

            # Create a token provider callback for mid-sync refresh
            tm = self.get_token_manager()

            async def _refresh():
                return await tm.get_valid_access_token(user_id, knowledge_id)

            token_provider = _refresh

        worker = self.create_worker(
            knowledge_id=knowledge_id,
            sources=sources,
            access_token=effective_token,
            user_id=user_id,
            app=app,
            token_provider=token_provider,
        )

        return await worker.sync()


def get_sync_provider(provider_type: str) -> SyncProvider:
    """
    Factory function for sync providers.

    Follows the same pattern as get_storage_provider() in storage/provider.py.
    """
    if provider_type == 'onedrive':
        from open_webui.services.onedrive.provider import OneDriveSyncProvider

        return OneDriveSyncProvider()
    elif provider_type == 'google_drive':
        from open_webui.services.google_drive.provider import GoogleDriveSyncProvider

        return GoogleDriveSyncProvider()
    else:
        raise ValueError(f'Unsupported sync provider: {provider_type}')


def get_token_manager(provider_type: str) -> TokenManager:
    """Factory function for token managers."""
    if provider_type == 'onedrive':
        from open_webui.services.onedrive.provider import OneDriveTokenManager

        return OneDriveTokenManager()
    elif provider_type == 'google_drive':
        from open_webui.services.google_drive.provider import GoogleDriveTokenManager

        return GoogleDriveTokenManager()
    else:
        raise ValueError(f'Unsupported token manager: {provider_type}')
