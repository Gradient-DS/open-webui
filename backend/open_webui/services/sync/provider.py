"""
Sync Provider Abstraction Layer.

Defines interfaces for external datasource sync providers.
Follows the factory-singleton pattern used by StorageProvider and VectorDBBase.

To add a new datasource:
1. Create a new directory under services/ (e.g., services/google_drive/)
2. Implement SyncProvider and TokenManager ABCs
3. Add a case to get_sync_provider() and get_token_manager()
4. Add the provider type to the Knowledge model's type validation
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict, Any


class TokenManager(ABC):
    """Manages OAuth token lifecycle for a sync provider."""

    @abstractmethod
    async def get_valid_access_token(
        self, user_id: str, knowledge_id: str
    ) -> Optional[str]:
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
    """Executes sync operations for an external datasource."""

    @abstractmethod
    async def execute_sync(
        self,
        knowledge_id: str,
        user_id: str,
        app,
        access_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Execute a sync for a knowledge base.

        Args:
            knowledge_id: The knowledge base ID
            user_id: The user who owns the KB
            app: FastAPI app instance (for process_file mock requests)
            access_token: Optional frontend-provided token (manual sync).
                         If None, the provider should obtain a token from
                         its TokenManager.

        Returns:
            Dict with sync results (files_processed, files_failed, etc.)
        """
        ...

    @abstractmethod
    def get_provider_type(self) -> str:
        """Return the provider type string (e.g., 'onedrive')."""
        ...

    @abstractmethod
    def get_token_manager(self) -> TokenManager:
        """Return the token manager for this provider."""
        ...


def get_sync_provider(provider_type: str) -> SyncProvider:
    """
    Factory function for sync providers.

    Follows the same pattern as get_storage_provider() in storage/provider.py.
    """
    if provider_type == "onedrive":
        from open_webui.services.onedrive.provider import OneDriveSyncProvider
        return OneDriveSyncProvider()
    # elif provider_type == "google_drive":
    #     from open_webui.services.google_drive.provider import GoogleDriveSyncProvider
    #     return GoogleDriveSyncProvider()
    else:
        raise ValueError(f"Unsupported sync provider: {provider_type}")


def get_token_manager(provider_type: str) -> TokenManager:
    """Factory function for token managers."""
    if provider_type == "onedrive":
        from open_webui.services.onedrive.provider import OneDriveTokenManager
        return OneDriveTokenManager()
    else:
        raise ValueError(f"Unsupported token manager: {provider_type}")
