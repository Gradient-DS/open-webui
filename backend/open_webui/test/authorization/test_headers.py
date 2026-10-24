"""Identity forwarding comes from the authenticated user, not incoming headers."""

from types import SimpleNamespace
from urllib.parse import quote

import jwt
import pytest


@pytest.fixture
def identity_headers(application, monkeypatch):
    from open_webui.utils import headers

    monkeypatch.setattr(headers, 'FORWARD_USER_INFO_HEADER_JWT_SECRET', '')
    return headers


@pytest.mark.parametrize('name', ('Alice Example', '  Zoë 李 / +  '))
def test_plain_identity_headers(identity_headers, name):
    module = identity_headers
    user = SimpleNamespace(id='owner', name=name, email=' owner@acl.invalid ', role='user')
    original = {'Content-Type': 'application/json'}
    assert module.include_user_info_headers(original, user) == {
        **original,
        module.FORWARD_USER_INFO_HEADER_USER_NAME: quote(name.strip(), safe=' '),
        module.FORWARD_USER_INFO_HEADER_USER_ID: 'owner',
        module.FORWARD_USER_INFO_HEADER_USER_EMAIL: 'owner@acl.invalid',
        module.FORWARD_USER_INFO_HEADER_USER_ROLE: 'user',
    }
    assert original == {'Content-Type': 'application/json'}


@pytest.mark.parametrize('explicit_none', (False, True))
def test_missing_user_adds_no_identity(identity_headers, explicit_none):
    original = {'Content-Type': 'application/json'}
    args = (original, None) if explicit_none else (original,)
    assert identity_headers.include_user_info_headers(*args) == original


def test_signed_identity_headers(identity_headers, monkeypatch):
    module = identity_headers
    monkeypatch.setattr(module, 'FORWARD_USER_INFO_HEADER_JWT_SECRET', 'forwarding-test-secret')
    user = SimpleNamespace(id='owner', name='Zoë 李', email='owner@acl.invalid', role='user')
    forwarded = module.include_user_info_headers({}, user)
    assert set(forwarded) == {module.FORWARD_USER_INFO_HEADER_JWT}
    claims = jwt.decode(
        forwarded[module.FORWARD_USER_INFO_HEADER_JWT],
        'forwarding-test-secret',
        algorithms=['HS256'],
        issuer='open-webui',
    )
    assert {key: claims[key] for key in ('sub', 'name', 'email', 'role')} == {
        'sub': user.id,
        'name': user.name,
        'email': user.email,
        'role': user.role,
    }
    assert claims['exp'] - claims['iat'] == module.FORWARD_USER_INFO_HEADER_JWT_EXPIRES_SECONDS
