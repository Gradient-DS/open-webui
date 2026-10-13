"""Integration tests for the Entra tenant allowlist on the OAuth login path.

These assert the seam between ``OAuthManager`` and authlib: that a tenant
allowlist produces ``claims_options`` with an ``iss`` validator, that the
validator accepts and rejects the right tenants, and that a rejection is
logged as the audit record of a confinement control firing.

``OAuthManager`` is built with ``__new__`` so no OAuth clients are registered.
"""

from __future__ import annotations

from types import SimpleNamespace

from open_webui.utils.oauth import OAuthManager

TENANT_A = 'aaaaaaaa-0000-4000-8000-000000000001'
TENANT_B = 'bbbbbbbb-0000-4000-8000-000000000002'
OTHER = '11111111-2222-3333-4444-555555555555'
LOGIN_BASE = 'https://login.microsoftonline.com'


def _auth_config(allowed_tenants):
    return SimpleNamespace(OAUTH_ALLOWED_TENANTS=allowed_tenants)


def _validator(manager, allowed_tenants):
    options = manager._microsoft_claims_options(_auth_config(allowed_tenants))
    assert options is not None
    return options['iss']['validate']


def test_no_allowlist_returns_none():
    manager = OAuthManager.__new__(OAuthManager)
    assert manager._microsoft_claims_options(_auth_config([])) is None


def test_iss_marked_essential():
    manager = OAuthManager.__new__(OAuthManager)
    options = manager._microsoft_claims_options(_auth_config([TENANT_A]))
    assert options['iss']['essential'] is True


def test_validator_accepts_listed_tenant():
    manager = OAuthManager.__new__(OAuthManager)
    validate = _validator(manager, [TENANT_A, TENANT_B])
    assert validate({'tid': TENANT_A}, f'{LOGIN_BASE}/{TENANT_A}/v2.0') is True


def test_validator_accepts_operator_tenant():
    manager = OAuthManager.__new__(OAuthManager)
    validate = _validator(manager, [TENANT_A, TENANT_B])
    assert validate({'tid': TENANT_B}, f'{LOGIN_BASE}/{TENANT_B}/v2.0') is True


def test_validator_rejects_unlisted_tenant():
    manager = OAuthManager.__new__(OAuthManager)
    validate = _validator(manager, [TENANT_A, TENANT_B])
    assert validate({'tid': OTHER}, f'{LOGIN_BASE}/{OTHER}/v2.0') is False


def test_validator_rejects_template_issuer():
    """The exact failure this whole change exists to fix."""
    manager = OAuthManager.__new__(OAuthManager)
    validate = _validator(manager, [TENANT_A])
    assert validate({'tid': TENANT_A}, f'{LOGIN_BASE}/{{tenantid}}/v2.0') is False


def test_rejection_is_logged(caplog):
    manager = OAuthManager.__new__(OAuthManager)
    validate = _validator(manager, [TENANT_A])
    with caplog.at_level('WARNING'):
        validate({'tid': OTHER}, f'{LOGIN_BASE}/{OTHER}/v2.0')
    assert 'OAUTH_ALLOWED_TENANTS' in caplog.text
    assert OTHER in caplog.text
