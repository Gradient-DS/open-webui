"""Unit tests for multi-tenant Entra issuer validation."""

from __future__ import annotations

import pytest

from open_webui.utils.oauth_issuer import MULTI_TENANT_AUTHORITIES, is_issuer_allowed

LOGIN_BASE = 'https://login.microsoftonline.com'
TENANT_A = 'aaaaaaaa-0000-4000-8000-000000000001'
TENANT_B = 'bbbbbbbb-0000-4000-8000-000000000002'
ALLOWED = [TENANT_A, TENANT_B]


def _iss(tenant: str) -> str:
    return f'{LOGIN_BASE}/{tenant}/v2.0'


def test_allows_listed_tenant():
    assert is_issuer_allowed(_iss(TENANT_A), TENANT_A, LOGIN_BASE, ALLOWED) is True


def test_allows_second_listed_tenant():
    assert is_issuer_allowed(_iss(TENANT_B), TENANT_B, LOGIN_BASE, ALLOWED) is True


def test_rejects_unlisted_tenant():
    other = '11111111-2222-3333-4444-555555555555'
    assert is_issuer_allowed(_iss(other), other, LOGIN_BASE, ALLOWED) is False


def test_rejects_missing_tid():
    assert is_issuer_allowed(_iss(TENANT_A), None, LOGIN_BASE, ALLOWED) is False


def test_rejects_tid_disagreeing_with_issuer():
    assert is_issuer_allowed(_iss(TENANT_A), TENANT_B, LOGIN_BASE, ALLOWED) is False


def test_rejects_literal_template_issuer():
    assert is_issuer_allowed(_iss('{tenantid}'), TENANT_A, LOGIN_BASE, ALLOWED) is False


def test_rejects_foreign_host():
    assert is_issuer_allowed(f'https://evil.example.com/{TENANT_A}/v2.0', TENANT_A, LOGIN_BASE, ALLOWED) is False


def test_rejects_host_prefix_attack():
    assert (
        is_issuer_allowed(f'https://login.microsoftonline.com.evil.com/{TENANT_A}/v2.0', TENANT_A, LOGIN_BASE, ALLOWED)
        is False
    )


def test_rejects_non_guid_tenant():
    assert is_issuer_allowed(f'{LOGIN_BASE}/contoso.onmicrosoft.com/v2.0', TENANT_A, LOGIN_BASE, ALLOWED) is False


def test_rejects_missing_v2_suffix():
    assert is_issuer_allowed(f'{LOGIN_BASE}/{TENANT_A}', TENANT_A, LOGIN_BASE, ALLOWED) is False


def test_rejects_none_issuer():
    assert is_issuer_allowed(None, TENANT_A, LOGIN_BASE, ALLOWED) is False


def test_rejects_empty_allowlist():
    assert is_issuer_allowed(_iss(TENANT_A), TENANT_A, LOGIN_BASE, []) is False


def test_case_insensitive_tenant_match():
    assert is_issuer_allowed(_iss(TENANT_A.upper()), TENANT_A.upper(), LOGIN_BASE, [TENANT_A.lower()]) is True


def test_tolerates_trailing_slash_on_login_base():
    assert is_issuer_allowed(_iss(TENANT_A), TENANT_A, LOGIN_BASE + '/', ALLOWED) is True


def test_tolerates_whitespace_in_allowlist_entries():
    assert is_issuer_allowed(_iss(TENANT_A), TENANT_A, LOGIN_BASE, [f'  {TENANT_A}  ']) is True


def test_multi_tenant_authorities_contents():
    assert MULTI_TENANT_AUTHORITIES == frozenset({'organizations', 'common'})


from open_webui.utils.oauth_issuer import require_tenant_allowlist


def test_guard_raises_for_organizations_without_allowlist():
    with pytest.raises(ValueError, match='OAUTH_ALLOWED_TENANTS'):
        require_tenant_allowlist('organizations', [])


def test_guard_raises_for_common_without_allowlist():
    with pytest.raises(ValueError, match='OAUTH_ALLOWED_TENANTS'):
        require_tenant_allowlist('common', [])


def test_guard_is_case_insensitive_on_authority():
    with pytest.raises(ValueError, match='OAUTH_ALLOWED_TENANTS'):
        require_tenant_allowlist('Organizations', [])


def test_guard_passes_for_organizations_with_allowlist():
    require_tenant_allowlist('organizations', [TENANT_A])


def test_guard_passes_for_concrete_tenant_without_allowlist():
    require_tenant_allowlist(TENANT_A, [])


def test_guard_passes_when_sso_not_configured():
    require_tenant_allowlist('', [])
    require_tenant_allowlist(None, [])
