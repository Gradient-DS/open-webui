"""Unit tests for multi-tenant Entra issuer validation."""

from __future__ import annotations

import pytest

from open_webui.utils.oauth_issuer import MULTI_TENANT_AUTHORITIES, is_issuer_allowed

LOGIN_BASE = 'https://login.microsoftonline.com'
NEO = 'cef7bed5-985e-4d2f-a5d6-1d70c527e7ab'
GRADIENT = 'cd143dbb-076f-4e0f-aa78-cc444d03b2da'
ALLOWED = [NEO, GRADIENT]


def _iss(tenant: str) -> str:
    return f'{LOGIN_BASE}/{tenant}/v2.0'


def test_allows_listed_tenant():
    assert is_issuer_allowed(_iss(NEO), NEO, LOGIN_BASE, ALLOWED) is True


def test_allows_second_listed_tenant():
    assert is_issuer_allowed(_iss(GRADIENT), GRADIENT, LOGIN_BASE, ALLOWED) is True


def test_rejects_unlisted_tenant():
    other = '11111111-2222-3333-4444-555555555555'
    assert is_issuer_allowed(_iss(other), other, LOGIN_BASE, ALLOWED) is False


def test_rejects_missing_tid():
    assert is_issuer_allowed(_iss(NEO), None, LOGIN_BASE, ALLOWED) is False


def test_rejects_tid_disagreeing_with_issuer():
    assert is_issuer_allowed(_iss(NEO), GRADIENT, LOGIN_BASE, ALLOWED) is False


def test_rejects_literal_template_issuer():
    assert is_issuer_allowed(_iss('{tenantid}'), NEO, LOGIN_BASE, ALLOWED) is False


def test_rejects_foreign_host():
    assert is_issuer_allowed(f'https://evil.example.com/{NEO}/v2.0', NEO, LOGIN_BASE, ALLOWED) is False


def test_rejects_host_prefix_attack():
    assert (
        is_issuer_allowed(f'https://login.microsoftonline.com.evil.com/{NEO}/v2.0', NEO, LOGIN_BASE, ALLOWED) is False
    )


def test_rejects_non_guid_tenant():
    assert is_issuer_allowed(f'{LOGIN_BASE}/contoso.onmicrosoft.com/v2.0', NEO, LOGIN_BASE, ALLOWED) is False


def test_rejects_missing_v2_suffix():
    assert is_issuer_allowed(f'{LOGIN_BASE}/{NEO}', NEO, LOGIN_BASE, ALLOWED) is False


def test_rejects_none_issuer():
    assert is_issuer_allowed(None, NEO, LOGIN_BASE, ALLOWED) is False


def test_rejects_empty_allowlist():
    assert is_issuer_allowed(_iss(NEO), NEO, LOGIN_BASE, []) is False


def test_case_insensitive_tenant_match():
    assert is_issuer_allowed(_iss(NEO.upper()), NEO.upper(), LOGIN_BASE, [NEO.lower()]) is True


def test_tolerates_trailing_slash_on_login_base():
    assert is_issuer_allowed(_iss(NEO), NEO, LOGIN_BASE + '/', ALLOWED) is True


def test_tolerates_whitespace_in_allowlist_entries():
    assert is_issuer_allowed(_iss(NEO), NEO, LOGIN_BASE, [f'  {NEO}  ']) is True


def test_multi_tenant_authorities_contents():
    assert MULTI_TENANT_AUTHORITIES == frozenset({'organizations', 'common'})
