"""TOPdesk auth helpers — header construction + readiness truth table.

These exercise the pure header builder (no config) plus the config-driven
helpers via monkeypatched PersistentConfig ``.value``. They run without
pytest-asyncio (no async here).
"""

from __future__ import annotations

import base64

from open_webui.services.topdesk import auth as topdesk_auth


def test_build_auth_header_basic_when_username_set():
    header = topdesk_auth.build_auth_header('operator', 's3cr3t')
    expected = 'Basic ' + base64.b64encode(b'operator:s3cr3t').decode('ascii')
    assert header == {'Authorization': expected}


def test_build_auth_header_is_basic_even_when_username_empty():
    # Basic-only (decision 4) — an empty login still produces a Basic header
    # (base64(":pw")); readiness gating (service_auth_configured) is what rejects
    # a missing login, not the header builder.
    header = topdesk_auth.build_auth_header('', 'app-pass-123')
    expected = 'Basic ' + base64.b64encode(b':app-pass-123').decode('ascii')
    assert header == {'Authorization': expected}


def test_build_auth_header_trims_username_whitespace():
    header = topdesk_auth.build_auth_header('  operator  ', 'pw')
    expected = 'Basic ' + base64.b64encode(b'operator:pw').decode('ascii')
    assert header == {'Authorization': expected}


class _Cfg:
    """Minimal stand-in for a PersistentConfig exposing ``.value``."""

    def __init__(self, value):
        self.value = value


def _patch_config(monkeypatch, url='', username='', app_password=''):
    monkeypatch.setattr(topdesk_auth, 'TOPDESK_URL', _Cfg(url))
    monkeypatch.setattr(topdesk_auth, 'TOPDESK_USERNAME', _Cfg(username))
    monkeypatch.setattr(topdesk_auth, 'TOPDESK_APP_PASSWORD', _Cfg(app_password))


def test_service_auth_configured_truth_table(monkeypatch):
    # URL + username + app_password all set → configured.
    _patch_config(monkeypatch, url='https://t.topdesk.net', username='op', app_password='pw')
    assert topdesk_auth.service_auth_configured() is True

    # Missing operator login → NOT configured (Basic-only requires the login).
    _patch_config(monkeypatch, url='https://t.topdesk.net', username='', app_password='pw')
    assert topdesk_auth.service_auth_configured() is False

    # Missing URL → not configured.
    _patch_config(monkeypatch, url='', username='op', app_password='pw')
    assert topdesk_auth.service_auth_configured() is False

    # Missing app_password → not configured.
    _patch_config(monkeypatch, url='https://t.topdesk.net', username='op', app_password='')
    assert topdesk_auth.service_auth_configured() is False

    # Whitespace-only values → not configured.
    _patch_config(monkeypatch, url='   ', username='   ', app_password='   ')
    assert topdesk_auth.service_auth_configured() is False


def test_auth_headers_reads_config_basic_form(monkeypatch):
    _patch_config(monkeypatch, url='https://t.topdesk.net', username=' op ', app_password='pw')
    header = topdesk_auth.auth_headers()
    assert header == {'Authorization': 'Basic ' + base64.b64encode(b'op:pw').decode('ascii')}


def test_get_service_site_derives_host(monkeypatch):
    _patch_config(monkeypatch, url='https://tenant.topdesk.net/', username='op', app_password='pw')
    site = topdesk_auth.get_service_site()
    assert site == {
        'cloud_id': 'tenant.topdesk.net',
        'url': 'https://tenant.topdesk.net',
        'name': 'tenant.topdesk.net',
    }


def test_get_service_site_none_when_url_missing(monkeypatch):
    _patch_config(monkeypatch, url='', username='op', app_password='pw')
    assert topdesk_auth.get_service_site() is None


def test_build_client_uses_config(monkeypatch):
    _patch_config(monkeypatch, url='https://tenant.topdesk.net/', username='op', app_password='pw')
    client = topdesk_auth.build_client()
    assert client.base_url == 'https://tenant.topdesk.net'
    # KB API path comes from config default; header is always Basic.
    assert client._auth_header == {'Authorization': 'Basic ' + base64.b64encode(b'op:pw').decode('ascii')}
