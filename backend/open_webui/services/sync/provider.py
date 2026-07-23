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

from abc import ABC, abstractmethod
from typing import Optional


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
    # Direct-upload via the distributed doc-pipeline. NOT a managed-sync
    # provider (no worker class) — the empty prefix is deliberate. A
    # direct-upload File row already exists with a bare UUID id; the
    # pipeline POSTs the parsed chunks back through /ingest with
    # acting_provider='owui_upload' and document.source_id=<file_id>, so an
    # empty prefix makes the reconstruction f'{prefix}{source_id}' an
    # identity and warren updates the existing row instead of creating a twin.
    'owui_upload': '',
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
    async def has_stored_token(self, user_id: str, knowledge_id: str) -> bool:
        """Check if a stored token exists (may be expired)."""
        ...

    @abstractmethod
    async def delete_token(self, user_id: str, knowledge_id: str) -> bool:
        """Delete stored token. Returns True if deleted."""
        ...


class SyncProvider(ABC):
    """Executes sync operations for an external datasource.

    Subclasses must implement:
    - get_provider_type() -> str
    - get_meta_key() -> str
    - get_token_manager() -> TokenManager
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
