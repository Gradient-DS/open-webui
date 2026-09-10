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

NEO = 'cef7bed5-985e-4d2f-a5d6-1d70c527e7ab'
GRADIENT = 'cd143dbb-076f-4e0f-aa78-cc444d03b2da'
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
    options = manager._microsoft_claims_options(_auth_config([NEO]))
    assert options['iss']['essential'] is True


def test_validator_accepts_listed_tenant():
    manager = OAuthManager.__new__(OAuthManager)
    validate = _validator(manager, [NEO, GRADIENT])
    assert validate({'tid': NEO}, f'{LOGIN_BASE}/{NEO}/v2.0') is True


def test_validator_accepts_operator_tenant():
    manager = OAuthManager.__new__(OAuthManager)
    validate = _validator(manager, [NEO, GRADIENT])
    assert validate({'tid': GRADIENT}, f'{LOGIN_BASE}/{GRADIENT}/v2.0') is True


def test_validator_rejects_unlisted_tenant():
    manager = OAuthManager.__new__(OAuthManager)
    validate = _validator(manager, [NEO, GRADIENT])
    assert validate({'tid': OTHER}, f'{LOGIN_BASE}/{OTHER}/v2.0') is False


def test_validator_rejects_template_issuer():
    """The exact failure this whole change exists to fix."""
    manager = OAuthManager.__new__(OAuthManager)
    validate = _validator(manager, [NEO])
    assert validate({'tid': NEO}, f'{LOGIN_BASE}/{{tenantid}}/v2.0') is False


def test_rejection_is_logged(caplog):
    manager = OAuthManager.__new__(OAuthManager)
    validate = _validator(manager, [NEO])
    with caplog.at_level('WARNING'):
        validate({'tid': OTHER}, f'{LOGIN_BASE}/{OTHER}/v2.0')
    assert 'OAUTH_ALLOWED_TENANTS' in caplog.text
    assert OTHER in caplog.text
