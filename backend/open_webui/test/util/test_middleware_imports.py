"""Regression guard for imports dropped in the v0.10.2 merge (middleware.py).

middleware.py's function bodies reference these names at runtime only, so a
dropped import survives `import open_webui.main` and every existing test, then
NameErrors the first real chat request ("name 'StreamingResponse' is not
defined" on /api/chat/completions). Assert they exist as module attributes so
the loss is caught at test time, not first-request time.
"""

from open_webui.utils import middleware


def test_streaming_response_imported():
    """process_chat_response wraps every streamed completion in StreamingResponse."""
    assert hasattr(middleware, 'StreamingResponse')


def test_html_module_imported():
    """Source/citation blocks html.escape() their payloads."""
    assert hasattr(middleware, 'html')


def test_post_webhook_imported():
    """Inactive-user webhook notifications call post_webhook (both merge parents imported it)."""
    assert hasattr(middleware, 'post_webhook')
