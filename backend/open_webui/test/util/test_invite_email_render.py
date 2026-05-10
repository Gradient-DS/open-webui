"""Tests for the invite email renderer's new SSO mode.

The new ``oauth_signup_enabled`` flag on ``render_invite_email`` swaps in
SSO-aware copy (button label and body sentence) for both English and
Dutch. These tests pin that behaviour so a future translation tweak can't
silently regress it.
"""

from __future__ import annotations

from open_webui.services.email.graph_mail_client import render_invite_email


def test_renders_password_copy_when_oauth_signup_disabled():
    html = render_invite_email(
        invite_url='https://example.com/auth/invite/tok',
        invited_by_name='Alice',
        locale='en',
        oauth_signup_enabled=False,
    )
    assert 'create your account' in html
    assert 'Accept Invite' in html
    assert 'Sign in with Microsoft' not in html


def test_renders_sso_copy_when_oauth_signup_enabled():
    html = render_invite_email(
        invite_url='https://example.com/auth/invite/tok',
        invited_by_name='Alice',
        locale='en',
        oauth_signup_enabled=True,
    )
    assert 'sign in and activate your account' in html
    assert 'Sign in with Microsoft' in html
    # The password-flow copy should not appear.
    assert 'create your account' not in html


def test_renders_dutch_sso_copy():
    html = render_invite_email(
        invite_url='https://example.com/auth/invite/tok',
        invited_by_name='Alice',
        locale='nl',
        oauth_signup_enabled=True,
    )
    assert 'Aanmelden met Microsoft' in html
    assert 'in te loggen en je account te activeren' in html


def test_renders_dutch_password_copy_by_default():
    html = render_invite_email(
        invite_url='https://example.com/auth/invite/tok',
        invited_by_name='Alice',
        locale='nl',
    )
    assert 'Uitnodiging accepteren' in html
    assert 'je account aan te maken' in html
    assert 'Aanmelden met Microsoft' not in html
