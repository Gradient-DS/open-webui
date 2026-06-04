"""Tests that the vector factory selects the correct Weaviate connector based on
ENABLE_WEAVIATE_MULTITENANCY_MODE.

The factory instantiates VECTOR_DB_CLIENT at module import time, which would
connect to Weaviate.  To keep tests self-contained we:

1. Import the factory module lazily (inside each test function) so we can set up
   monkeypatches first.
2. Monkeypatch both WeaviateClient.__init__ methods to no-ops so no real
   connection is made.
3. Patch ENABLE_WEAVIATE_MULTITENANCY_MODE on the already-imported factory
   module to flip the flag after the initial module import.
4. Call Vector.get_vector('weaviate') directly (not via the module-level
   VECTOR_DB_CLIENT) and assert the returned instance's module.
"""

import importlib
import sys


def _noop_init(self) -> None:  # noqa: D401
    """Replacement __init__ that skips the real Weaviate connection."""
    pass


def _import_factory_with_noop_weaviate(monkeypatch):
    """Import (or reimport) factory with both WeaviateClient.__init__ patched.

    The factory module is reimported fresh each time so that monkeypatches on
    the factory module's own names take effect predictably.  Both legacy and MT
    connectors are pre-imported and patched before the factory module loads, so
    the lazy imports inside the match-case branches find the patched classes.
    """
    # Pre-import the connector modules so we can monkeypatch them before the
    # factory's lazy imports run.
    import open_webui.retrieval.vector.dbs.weaviate as legacy_mod
    import open_webui.retrieval.vector.dbs.weaviate_multitenancy as mt_mod

    monkeypatch.setattr(legacy_mod.WeaviateClient, '__init__', _noop_init)
    monkeypatch.setattr(mt_mod.WeaviateClient, '__init__', _noop_init)

    # Force a fresh import of the factory so we get a clean module reference.
    factory_key = 'open_webui.retrieval.vector.factory'
    if factory_key in sys.modules:
        del sys.modules[factory_key]

    import open_webui.retrieval.vector.factory as factory_mod

    return factory_mod


class TestWeaviateFactorySelection:
    """Flag-driven connector selection."""

    def test_flag_off_returns_legacy_connector(self, monkeypatch):
        """With ENABLE_WEAVIATE_MULTITENANCY_MODE=False the legacy connector is returned."""
        factory_mod = _import_factory_with_noop_weaviate(monkeypatch)

        # Ensure the flag is False on the factory module (it reads the module-level name).
        monkeypatch.setattr(factory_mod, 'ENABLE_WEAVIATE_MULTITENANCY_MODE', False)

        instance = factory_mod.Vector.get_vector('weaviate')

        assert instance.__class__.__module__.endswith('.weaviate'), (
            f'Expected legacy weaviate module, got {instance.__class__.__module__!r}'
        )
        assert instance.__class__.__module__ == 'open_webui.retrieval.vector.dbs.weaviate'

    def test_flag_on_returns_mt_connector(self, monkeypatch):
        """With ENABLE_WEAVIATE_MULTITENANCY_MODE=True the MT connector is returned."""
        factory_mod = _import_factory_with_noop_weaviate(monkeypatch)

        monkeypatch.setattr(factory_mod, 'ENABLE_WEAVIATE_MULTITENANCY_MODE', True)

        instance = factory_mod.Vector.get_vector('weaviate')

        assert instance.__class__.__module__.endswith('.weaviate_multitenancy'), (
            f'Expected weaviate_multitenancy module, got {instance.__class__.__module__!r}'
        )
        assert instance.__class__.__module__ == 'open_webui.retrieval.vector.dbs.weaviate_multitenancy'

    def test_flag_off_class_name_is_weaviate_client(self, monkeypatch):
        """Sanity-check: the class name is WeaviateClient in both paths."""
        factory_mod = _import_factory_with_noop_weaviate(monkeypatch)
        monkeypatch.setattr(factory_mod, 'ENABLE_WEAVIATE_MULTITENANCY_MODE', False)
        instance = factory_mod.Vector.get_vector('weaviate')
        assert instance.__class__.__name__ == 'WeaviateClient'

    def test_flag_on_class_name_is_weaviate_client(self, monkeypatch):
        """Sanity-check: the class name is WeaviateClient in both paths."""
        factory_mod = _import_factory_with_noop_weaviate(monkeypatch)
        monkeypatch.setattr(factory_mod, 'ENABLE_WEAVIATE_MULTITENANCY_MODE', True)
        instance = factory_mod.Vector.get_vector('weaviate')
        assert instance.__class__.__name__ == 'WeaviateClient'
