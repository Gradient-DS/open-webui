"""Tests for the password-reset email renderer (EN + NL)."""

from __future__ import annotations

from open_webui.services.email.graph_mail_client import (
    render_password_reset_email,
    render_password_reset_subject,
)


def test_renders_english_reset_email():
    html = render_password_reset_email(
        reset_url='https://example.com/auth/reset-password/tok',
        locale='en',
        expiry_minutes=30,
    )
    assert 'https://example.com/auth/reset-password/tok' in html
    assert 'Reset password' in html
    assert '30 minutes' in html


def test_renders_dutch_reset_email():
    html = render_password_reset_email(
        reset_url='https://example.com/auth/reset-password/tok',
        locale='nl',
        expiry_minutes=30,
    )
    assert 'Wachtwoord resetten' in html
    assert '30 minuten' in html


def test_reset_subject_localised():
    assert 'Reset' in render_password_reset_subject(locale='en')
    assert 'Reset' in render_password_reset_subject(locale='nl')  # Dutch copy also contains "Reset je ..."
