"""
Sync Provider Abstraction Layer.

Defines interfaces for external datasource sync providers.
Follows the factory-singleton pattern used by StorageProvider and VectorDBBase.

To add a new managed-sync datasource:
1. Create a new directory under services/ (e.g., services/dropbox/)
2. Subclass SyncProvider and TokenManager
3. Add a case to get_sync_provider() and get_token_manager()
4. Add the provider type to the Knowledge model's type validation
5. Add an entry to PROVIDER_FILE_ID_PREFIXES below mapping the
   provider_slug (your get_provider_type() return value) to the
   file_id_prefix property of your worker — only required when the
   slug differs from the prefix.rstrip('-') (e.g. Google Drive's
   ``google_drive`` slug vs ``googledrive-`` prefix). External push
   providers (admin-configured via INTEGRATION_PROVIDERS, no worker
   class) need no entry — file_id_prefix_for() falls back to
   ``f'{slug}-'`` for them.
"""

import logging
import time
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any

from open_webui.models.knowledge import Knowledges

log = logging.getLogger(__name__)


# Maps managed-sync provider_slug → file_id_prefix used by that provider's
# worker class when inserting stub File rows. The two strings need not match
# (and don't, for Google Drive) — the loader-worker echoes provider_slug back
# in the /ingest callback, but the stub File row was inserted with
# file_id_prefix. This registry lets the ingest endpoint reconstruct the
# correct file_id for managed-sync round-trips.
#
# External push providers (admin-configured via INTEGRATION_PROVIDERS) have
# no worker class and no stub creation, so there's no slug/prefix divergence
# to override — they default to f'{slug}-' via the fallback in
# file_id_prefix_for() below.
PROVIDER_FILE_ID_PREFIXES: dict[str, str] = {
    'onedrive': 'onedrive-',
    'google_drive': 'googledrive-',
    'confluence': 'confluence-',
}


def file_id_prefix_for(provider_slug: str) -> str:
    """Return the file_id prefix for ``provider_slug``.

    For managed-sync providers (onedrive, google_drive, confluence) returns
    the registry value, which may differ from the slug (Google Drive's
    ``google_drive`` slug maps to the ``googledrive-`` prefix). For any
    other slug — admin-configured external push providers in
    ``INTEGRATION_PROVIDERS`` — falls back to ``f'{slug}-'``, the
    pre-cc24c435b convention where the slug *is* the prefix.

    This is a total function: never raises. Push-provider auth is enforced
    by ``routers.integrations.get_integration_provider`` (403 on unknown
    slug) and KB-creation is gated by ``routers.knowledge``'s
    ``allowed_kb_types`` check; this helper only computes the prefix.
    """
    return PROVIDER_FILE_ID_PREFIXES.get(provider_slug, f'{provider_slug}-')


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

        # A provider may resolve its source list dynamically at sync time —
        # the Confluence shared KB has no stored sources but flags `shared`
        # in its meta and resolves the selected spaces on each run.
        if not sources and not sync_info.get('shared'):
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

        result = await worker.sync()

        # Stamp the completion time so the admin UI reflects every run —
        # including no-op syncs where 0 files changed. base_worker records
        # this on its normal completion path; doing it here guarantees the
        # timestamp advances for every provider and every non-error exit,
        # and clears a status left stuck on 'syncing'.
        if isinstance(result, dict) and not result.get('error') and not result.get('suspended'):
            try:
                kb = Knowledges.get_knowledge_by_id(id=knowledge_id)
                if kb:
                    meta = kb.meta or {}
                    sync_info = meta.get(self.get_meta_key(), {})
                    sync_info['last_sync_at'] = int(time.time())
                    if sync_info.get('status') in (None, 'idle', 'syncing'):
                        sync_info['status'] = 'completed'
                    meta[self.get_meta_key()] = sync_info
                    Knowledges.update_knowledge_meta_by_id(knowledge_id, meta)
            except Exception:
                log.exception('Failed to stamp sync completion for KB %s', knowledge_id)

        return result


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
