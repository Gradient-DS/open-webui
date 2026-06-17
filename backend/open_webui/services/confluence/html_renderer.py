"""Back-compat shim — the HTML → Markdown renderer moved to the shared sync layer.

The renderer is now provider-agnostic and lives at
``open_webui.services.sync.html_renderer``. This module re-exports it so existing
Confluence imports (``services.confluence.sync_worker``, ``routers.confluence_sync``)
keep working unchanged.
"""

from open_webui.services.sync.html_renderer import html_to_markdown  # noqa: F401

__all__ = ['html_to_markdown']
