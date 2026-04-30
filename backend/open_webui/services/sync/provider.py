"""
Sync Provider Abstraction Layer.

Defines interfaces for external datasource sync providers.
Follows the factory-singleton pattern used by StorageProvider and VectorDBBase.

To add a new datasource:
1. Create a new directory under services/ (e.g., services/dropbox/)
2. Subclass SyncProvider and TokenManager
3. Add a case to get_sync_provider() and get_token_manager()
4. Add the provider type to the Knowledge model's type validation
5. Add an entry to PROVIDER_FILE_ID_PREFIXES below mapping the
   provider_slug (your get_provider_type() return value) to the
   file_id_prefix property of your worker. Do NOT assume
   slug == prefix.rstrip('-') — that invariant is no longer
   enforced anywhere; this map is the single source of truth.
"""

import logging
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any

from open_webui.models.knowledge import Knowledges

log = logging.getLogger(__name__)


# Maps provider_slug (the X-Acting-Provider header value, also the
# `provider_type` returned by SyncProvider.get_provider_type()) to the
# file_id_prefix used by the worker when inserting stub File rows.
#
# The two strings need not match (and don't, for Google Drive) — the
# loader-worker echoes provider_slug back in the /ingest callback, but the
# stub File row was inserted with file_id_prefix. This registry lets the
# ingest endpoint reconstruct the correct file_id without each provider
# having to choose slug == prefix.rstrip('-').
PROVIDER_FILE_ID_PREFIXES: dict[str, str] = {
    'onedrive': 'onedrive-',
    'google_drive': 'googledrive-',
    'confluence': 'confluence-',
}


def file_id_prefix_for(provider_slug: str) -> str:
    """Return the file_id_prefix used by the worker for ``provider_slug``.

    Raises ValueError on unknown slug. Callers (the ingest endpoint, mainly)
    should propagate that as a 400 — an unknown X-Acting-Provider header is
    a misconfigured loader-worker, not a per-document failure.
    """
    try:
        return PROVIDER_FILE_ID_PREFIXES[provider_slug]
    except KeyError as exc:
        raise ValueError(f'Unknown provider slug: {provider_slug!r}') from exc


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
        use_shared_loader: bool = False,
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

        # Read shared-loader flag from app config (set by main.py at startup).
        use_shared_loader = bool(getattr(app.state.config, 'USE_SHARED_LOADER', False))

        worker = self.create_worker(
            knowledge_id=knowledge_id,
            sources=sources,
            access_token=effective_token,
            user_id=user_id,
            app=app,
            token_provider=token_provider,
            use_shared_loader=use_shared_loader,
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
    elif provider_type == 'confluence':
        from open_webui.services.confluence.provider import ConfluenceSyncProvider

        return ConfluenceSyncProvider()
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
    elif provider_type == 'confluence':
        from open_webui.services.confluence.provider import ConfluenceTokenManager

        return ConfluenceTokenManager()
    else:
        raise ValueError(f'Unsupported token manager: {provider_type}')
