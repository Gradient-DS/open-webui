"""Caller-supplied values must not smuggle markup into rendered emails.

The invite / password-reset / retention bodies are assembled with f-strings, so
every value interpolated into them is escaped at the boundary. ``invited_by_name``
is a user's own display name (IdP-supplied in the SSO tenants), and the heading /
client name come from deployment config — none of them may introduce elements or
break out of the ``href`` attribute they land in.

The templates themselves intentionally carry markup (``APP_NAME_HTML`` is the
entity-bearing ``soev&#x2060;.ai``), so these tests also pin that escaping is
applied to the values only and never to the assembled string.
"""

from __future__ import annotations

from open_webui.services.email.graph_mail_client import (
    APP_NAME_HTML,
    render_invite_email,
    render_password_reset_email,
    render_retention_warning_email,
)

XSS = '<img src=x onerror=alert(1)>'


def test_invited_by_name_is_escaped():
    html = render_invite_email(
        invite_url='https://example.com/auth/invite/tok',
        invited_by_name=XSS,
        locale='en',
    )
    assert XSS not in html
    assert '&lt;img src=x onerror=alert(1)&gt;' in html


def test_custom_heading_is_escaped():
    html = render_invite_email(
        invite_url='https://example.com/auth/invite/tok',
        invited_by_name='Alice',
        locale='en',
        custom_heading='<script>alert(1)</script>',
    )
    assert '<script>' not in html
    assert '&lt;script&gt;' in html


def test_client_name_is_escaped():
    html = render_invite_email(
        invite_url='https://example.com/auth/invite/tok',
        invited_by_name='Alice',
        locale='en',
        client_name='<b>Acme</b>',
    )
    assert '<b>Acme</b>' not in html
    assert '&lt;b&gt;Acme&lt;/b&gt;' in html


def test_invite_url_cannot_break_out_of_the_href_attribute():
    html = render_invite_email(
        invite_url='https://example.com/"><script>alert(1)</script>',
        invited_by_name='Alice',
        locale='en',
    )
    assert '"><script>' not in html
    assert '&quot;&gt;&lt;script&gt;' in html


def test_reset_url_cannot_break_out_of_the_href_attribute():
    html = render_password_reset_email(
        reset_url='https://example.com/"><script>alert(1)</script>',
        locale='en',
    )
    assert '"><script>' not in html
    assert '&quot;&gt;&lt;script&gt;' in html


def test_login_url_cannot_break_out_of_the_href_attribute():
    html = render_retention_warning_email(
        login_url='https://example.com/"><script>alert(1)</script>',
        days_remaining=7,
        locale='en',
    )
    assert '"><script>' not in html
    assert '&quot;&gt;&lt;script&gt;' in html


def test_template_markup_is_preserved():
    """Escaping applies to the values, not to the template around them."""
    html = render_invite_email(
        invite_url='https://example.com/auth/invite/tok',
        invited_by_name='Alice',
        locale='en',
    )
    # The app-name entity and the surrounding layout survive untouched.
    assert APP_NAME_HTML in html
    assert '<!DOCTYPE html>' in html
    assert 'href="https://example.com/auth/invite/tok"' in html


def test_autolink_suppression_entity_survives_escaping():
    """_prevent_email_autolink runs after escaping, so its entity stays literal."""
    html = render_invite_email(
        invite_url='https://example.com/auth/invite/tok',
        invited_by_name='Alice',
        locale='en',
        custom_heading='Welcome to acme.com',
    )
    assert 'acme&#x2060;.com' in html
    assert 'acme&amp;#x2060;.com' not in html


def test_dutch_locale_is_escaped_too():
    html = render_invite_email(
        invite_url='https://example.com/auth/invite/tok',
        invited_by_name=XSS,
        locale='nl',
    )
    assert XSS not in html
