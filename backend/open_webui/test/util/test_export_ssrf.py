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


# ---------------------------------------------------------------------------
# Follow-ups from the security review of PR #270: the <img>/tag allowlist was
# sound, but three fetch vectors reached WeasyPrint and were stopped only by the
# safe_pdf_url_fetcher backstop. These pin them at the sanitiser, so the export
# HTML no longer depends on a second control.
# ---------------------------------------------------------------------------


class TestCssUrlAllowlist:
    """Every url() in an inline style must be a data: URI — not merely one of them."""

    def test_mixed_data_and_remote_urls_drops_the_style(self):
        html = sanitize_export_html(
            '<div style="background:url(data:image/png;base64,A);'
            'list-style-image:url(http://attacker.example/leak.png)">x</div>'
        )
        assert 'attacker.example' not in html
        assert 'style=' not in html

    def test_css_comment_cannot_fake_a_data_url(self):
        """`/*url(data:*/` made the old substring check see a data URI."""
        html = sanitize_export_html(
            '<div style="/*url(data:*/background:url(http://attacker.example/leak.png)">x</div>'
        )
        assert 'attacker.example' not in html
        assert 'style=' not in html

    def test_quoted_and_uppercase_remote_urls_are_caught(self):
        # A double-quoted CSS url needs a single-quoted HTML attribute to be
        # well-formed; nesting like-for-like just terminates the attribute.
        for markup in (
            """<div style="background:url('http://attacker.example/x.png')">x</div>""",
            """<div style='background:url("http://attacker.example/x.png")'>x</div>""",
            """<div style="background:URL(HTTP://attacker.example/x.png)">x</div>""",
        ):
            html = sanitize_export_html(markup)
            assert 'attacker.example' not in html.lower(), markup
            assert 'style=' not in html, markup

    def test_unparseable_url_call_drops_the_style(self):
        """A url( the extractor cannot read is treated as unsafe, not ignored."""
        html = sanitize_export_html('<div style="background:url(">x</div>')
        assert 'style=' not in html

    def test_file_url_in_style_is_caught(self):
        html = sanitize_export_html('<div style="background:url(file:///etc/passwd)">x</div>')
        assert 'passwd' not in html

    def test_genuine_data_uri_style_is_preserved(self):
        html = sanitize_export_html('<div style="background:url(data:image/png;base64,AAAA)">x</div>')
        assert 'data:image/png;base64,AAAA' in html

    def test_style_without_any_url_is_preserved(self):
        html = sanitize_export_html('<div style="color:red;font-weight:bold">x</div>')
        assert 'color:red' in html


class TestSvgFetchVectors:
    """<svg> carries href-based fetches that the <img> filter never saw."""

    def test_svg_image_href_is_removed(self):
        html = sanitize_export_html('<svg><image href="http://attacker.example/x.png"/></svg>')
        assert 'attacker.example' not in html
        assert '<svg' not in html.lower()

    def test_svg_use_href_is_removed(self):
        html = sanitize_export_html('<svg><use href="file:///etc/passwd"/></svg>')
        assert 'passwd' not in html
        assert '<svg' not in html.lower()

    def test_svg_wrapping_an_image_is_removed_whole(self):
        html = sanitize_export_html('<svg><image xlink:href="http://attacker.example/x.png"/></svg>')
        assert 'attacker.example' not in html


class TestSurvivingImageCannotFetch:
    """An <img> kept for its data: src must not fetch via a second attribute."""

    def test_srcset_is_stripped_from_a_kept_image(self):
        html = sanitize_export_html('<img src="data:image/png;base64,AAAA" srcset="http://attacker.example/x.png 2x">')
        assert 'attacker.example' not in html
        assert 'srcset' not in html
        # The legitimate inline image survives.
        assert 'data:image/png;base64,AAAA' in html

    def test_lowsrc_is_stripped_from_a_kept_image(self):
        html = sanitize_export_html('<img src="data:image/png;base64,AAAA" lowsrc="http://attacker.example/x.png">')
        assert 'attacker.example' not in html

    def test_input_type_image_is_removed(self):
        html = sanitize_export_html('<input type="image" src="http://attacker.example/x.png">')
        assert 'attacker.example' not in html

    def test_ordinary_input_is_left_alone(self):
        html = sanitize_export_html('<input type="text" value="hi">')
        assert 'value="hi"' in html
