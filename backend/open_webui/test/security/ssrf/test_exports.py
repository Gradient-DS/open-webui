"""Exercise the renderer URL callback used by chat and document PDF exports."""

import pytest
from open_webui.utils.chat_export import safe_pdf_url_fetcher

from .conftest import BLOCKED_URLS, PUBLIC


@pytest.mark.parametrize('url', [url for url in BLOCKED_URLS if not url.startswith('data:')] + [PUBLIC])
def test_export_renderer_refuses_external_resources(url, http_boundary):
    with pytest.raises(ValueError):
        safe_pdf_url_fetcher()(url)
    assert http_boundary.sent == []
