"""Regression tests: document/chat export must not fetch external or local resources.

The server-side PDF (WeasyPrint) and DOCX (htmldocx) export renderers dereference
resource URLs found in the export HTML — ``<img src>``, CSS ``url()``, ``<link>``.
Because markdown passes raw HTML through untouched, attacker-controlled (or
prompt-injected) markdown could turn an export into an SSRF (cloud metadata,
internal services) or a local-file read (``file://`` / absolute paths embedded
into the output document).

These tests pin the sanitisation choke point (``_md_to_html`` /
``sanitize_export_html``) and the WeasyPrint ``url_fetcher`` backstop so the fix
cannot silently regress.
"""

import pytest

from open_webui.utils.chat_export import (
    _md_to_html,
    safe_pdf_url_fetcher,
    sanitize_export_html,
)


# --- HTML sanitisation ---------------------------------------------------------


def test_remote_image_is_stripped():
    """An <img> pointing at an http(s) URL (SSRF vector) is removed entirely."""
    html = sanitize_export_html('<p><img src="http://169.254.169.254/latest/meta-data/"></p>')
    assert 'img' not in html
    assert '169.254.169.254' not in html


def test_file_scheme_image_is_stripped():
    """A file:// image (local-file read via WeasyPrint) is removed."""
    html = sanitize_export_html('<img src="file:///etc/passwd">')
    assert 'img' not in html
    assert 'passwd' not in html


def test_absolute_path_image_is_stripped():
    """A bare absolute path (local-file read via htmldocx) is removed."""
    html = sanitize_export_html('<img src="/app/backend/open_webui/static/favicon.png">')
    assert 'img' not in html


def test_data_image_uri_is_preserved():
    """Inline data:image/ URIs are self-contained and must survive sanitisation."""
    tiny = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=='
    html = sanitize_export_html(f'<img src="{tiny}">')
    assert 'data:image/png' in html


def test_non_image_data_uri_is_stripped():
    """data: URIs that are not images (e.g. data:text/html) are not allowed as <img>."""
    html = sanitize_export_html('<img src="data:text/html,<script>alert(1)</script>">')
    assert 'img' not in html


def test_hyperlink_is_preserved():
    """<a href> is inert (renderers do not fetch href) and must not be stripped."""
    html = sanitize_export_html('<a href="http://internal.example/secret">click</a>')
    assert 'href="http://internal.example/secret"' in html


@pytest.mark.parametrize('tag', ['link', 'style', 'script', 'iframe', 'object', 'embed', 'base'])
def test_resource_fetching_tags_are_stripped(tag):
    """Tags that can pull in an external resource are removed."""
    html = sanitize_export_html(f'<{tag} src="http://internal/x" href="http://internal/y">body</{tag}>')
    assert f'<{tag}' not in html.lower()
    assert 'internal' not in html


def test_link_attachment_exfil_vector_is_stripped():
    """<link rel="attachment" href="file://..."> makes WeasyPrint embed the *entire*
    target file (e.g. credentials) into the PDF as an attachment — arbitrary file
    disclosure, not just blind SSRF. The <link> element must be removed."""
    html = sanitize_export_html('<link rel="attachment" href="file:///proc/self/environ">')
    assert '<link' not in html.lower()
    assert 'environ' not in html


def test_inline_style_url_is_neutralised():
    """A CSS url() in an inline style (WeasyPrint fetch vector) is removed."""
    html = sanitize_export_html('<div style="background:url(http://169.254.169.254/)">x</div>')
    assert 'url(http' not in html.lower()
    assert '169.254.169.254' not in html


def test_inline_style_data_url_is_kept():
    """A data: url() in an inline style is safe and may stay."""
    html = sanitize_export_html('<div style="background:url(data:image/png;base64,AAAA)">x</div>')
    assert 'data:image/png' in html


# --- end-to-end through the markdown converter ---------------------------------


def test_md_to_html_strips_markdown_image_ssrf():
    """The markdown image syntax is the primary fetch vector and must be sanitised."""
    html = _md_to_html('![poc](http://169.254.169.254/latest/meta-data/)')
    assert 'img' not in html
    assert '169.254.169.254' not in html


def test_md_to_html_strips_markdown_image_local_file():
    """A markdown image pointing at a local path is sanitised (htmldocx LFI)."""
    html = _md_to_html('![poc](/app/backend/open_webui/static/favicon.png)')
    assert 'img' not in html


def test_md_to_html_keeps_markdown_link():
    """A markdown link renders to an inert <a> and is preserved."""
    html = _md_to_html('[click](http://internal.example/page)')
    assert 'href="http://internal.example/page"' in html


# --- WeasyPrint url_fetcher backstop ------------------------------------------


@pytest.mark.parametrize(
    'url',
    [
        'http://169.254.169.254/latest/meta-data/',
        'https://internal.svc.cluster.local/admin',
        'file:///etc/passwd',
        'ftp://internal/x',
        '/app/backend/open_webui/static/favicon.png',
    ],
)
def test_pdf_url_fetcher_blocks_non_data(url):
    """WeasyPrint must refuse to dereference anything but a data: URI."""
    with pytest.raises(ValueError):
        safe_pdf_url_fetcher(url)


def test_pdf_url_fetcher_allows_data_uri():
    """A data: URI is self-contained and is passed through to WeasyPrint."""
    pytest.importorskip('weasyprint')
    result = safe_pdf_url_fetcher(
        'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=='
    )
    assert isinstance(result, dict)
