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


def test_build_auth_header_token_form_when_username_empty():
    header = topdesk_auth.build_auth_header('', 'app-pass-123')
    assert header == {'Authorization': 'TOKEN id="app-pass-123"'}


def test_build_auth_header_treats_whitespace_username_as_empty():
    header = topdesk_auth.build_auth_header('   ', 'pw')
    assert header == {'Authorization': 'TOKEN id="pw"'}


class _Cfg:
    """Minimal stand-in for a PersistentConfig exposing ``.value``."""

    def __init__(self, value):
        self.value = value


def _patch_config(monkeypatch, url='', username='', api_token=''):
    monkeypatch.setattr(topdesk_auth, 'TOPDESK_URL', _Cfg(url))
    monkeypatch.setattr(topdesk_auth, 'TOPDESK_USERNAME', _Cfg(username))
    monkeypatch.setattr(topdesk_auth, 'TOPDESK_API_TOKEN', _Cfg(api_token))


def test_service_auth_configured_truth_table(monkeypatch):
    # URL + api_token set, username present → configured.
    _patch_config(monkeypatch, url='https://t.topdesk.net', username='op', api_token='pw')
    assert topdesk_auth.service_auth_configured() is True

    # URL + api_token set, username empty (TOKEN form) → still configured.
    _patch_config(monkeypatch, url='https://t.topdesk.net', username='', api_token='pw')
    assert topdesk_auth.service_auth_configured() is True

    # Missing URL → not configured.
    _patch_config(monkeypatch, url='', username='op', api_token='pw')
    assert topdesk_auth.service_auth_configured() is False

    # Missing api_token → not configured.
    _patch_config(monkeypatch, url='https://t.topdesk.net', username='op', api_token='')
    assert topdesk_auth.service_auth_configured() is False

    # Whitespace-only values → not configured.
    _patch_config(monkeypatch, url='   ', username='op', api_token='   ')
    assert topdesk_auth.service_auth_configured() is False


def test_auth_headers_reads_config_basic_form(monkeypatch):
    _patch_config(monkeypatch, url='https://t.topdesk.net', username=' op ', api_token='pw')
    header = topdesk_auth.auth_headers()
    assert header == {'Authorization': 'Basic ' + base64.b64encode(b'op:pw').decode('ascii')}


def test_auth_headers_reads_config_token_form(monkeypatch):
    _patch_config(monkeypatch, url='https://t.topdesk.net', username='', api_token='pw')
    assert topdesk_auth.auth_headers() == {'Authorization': 'TOKEN id="pw"'}


def test_get_service_site_derives_host(monkeypatch):
    _patch_config(monkeypatch, url='https://tenant.topdesk.net/', username='op', api_token='pw')
    site = topdesk_auth.get_service_site()
    assert site == {
        'cloud_id': 'tenant.topdesk.net',
        'url': 'https://tenant.topdesk.net',
        'name': 'tenant.topdesk.net',
    }


def test_get_service_site_none_when_url_missing(monkeypatch):
    _patch_config(monkeypatch, url='', username='op', api_token='pw')
    assert topdesk_auth.get_service_site() is None


def test_build_client_uses_config(monkeypatch):
    _patch_config(monkeypatch, url='https://tenant.topdesk.net/', username='op', api_token='pw')
    client = topdesk_auth.build_client()
    assert client.base_url == 'https://tenant.topdesk.net'
    # GraphQL path comes from config default; header is Basic since username is set.
    assert client._auth_header == {'Authorization': 'Basic ' + base64.b64encode(b'op:pw').decode('ascii')}
