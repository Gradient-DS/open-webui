"""
Permission Provider Registry

Central registry for permission providers. Providers register themselves
and can be looked up by source type.
"""

import logging
from typing import Dict, List, Optional

from open_webui.services.permissions.provider import PermissionProvider

log = logging.getLogger(__name__)


class PermissionProviderRegistry:
    """Central registry for permission providers."""

    _providers: Dict[str, PermissionProvider] = {}
    _initialized: bool = False

    @classmethod
    def register(cls, provider: PermissionProvider) -> None:
        """Register a permission provider."""
        cls._providers[provider.source_type] = provider
        log.info(f"Registered permission provider: {provider.source_type}")

    @classmethod
    def unregister(cls, source_type: str) -> None:
        """Unregister a permission provider."""
        if source_type in cls._providers:
            del cls._providers[source_type]
            log.info(f"Unregistered permission provider: {source_type}")

    @classmethod
    def get_provider(cls, source_type: str) -> Optional[PermissionProvider]:
        """Get a permission provider by source type."""
        return cls._providers.get(source_type)

    @classmethod
    def get_all_providers(cls) -> List[PermissionProvider]:
        """Get all registered permission providers."""
        return list(cls._providers.values())

    @classmethod
    def get_provider_for_file(cls, file_meta: dict) -> Optional[PermissionProvider]:
        """
        Get the appropriate provider for a file based on its metadata.

        Args:
            file_meta: File metadata dict containing 'source' key

        Returns:
            PermissionProvider if one exists for this source type
        """
        source = file_meta.get("source", "local")
        if source == "local":
            return None  # Local files have no source restrictions
        return cls.get_provider(source)

    @classmethod
    def has_provider(cls, source_type: str) -> bool:
        """Check if a provider is registered for the given source type."""
        return source_type in cls._providers

    @classmethod
    def clear(cls) -> None:
        """Clear all registered providers. Used for testing."""
        cls._providers.clear()
        cls._initialized = False
