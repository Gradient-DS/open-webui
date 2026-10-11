"""Identity forwarding comes from the authenticated user, not incoming headers."""

from types import SimpleNamespace
from urllib.parse import quote

import jwt
import pytest
from starlette.requests import Request


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


@pytest.mark.parametrize('signed', (False, True))
@pytest.mark.parametrize('principal', ('owner', None))
def test_openai_header_builder_discards_inbound_identity(seeded, identity_headers, monkeypatch, signed, principal):
    from open_webui.models.users import Users
    from open_webui.routers import openai

    module = identity_headers
    monkeypatch.setattr(openai, 'ENABLE_FORWARD_USER_INFO_HEADERS', True)
    monkeypatch.setattr(module, 'FORWARD_USER_INFO_HEADER_JWT_SECRET', 'forwarding-test-secret' if signed else '')
    spoof = {
        'x-openwebui-user-id': 'spoofed',
        'x-openwebui-user-name': 'spoofed',
        'x-openwebui-user-email': 'spoofed',
        'x-openwebui-user-role': 'spoofed',
        'x-openwebui-user-jwt': 'spoofed',
    }
    request = Request({'type': 'http', 'headers': [(key.encode(), value.encode()) for key, value in spoof.items()]})

    async def build():
        user = await Users.get_user_by_id(principal) if principal else None
        return await openai.get_headers_and_cookies(
            request, 'https://backend.invalid', key='backend-key', config={'auth_type': 'bearer'}, user=user
        )

    forwarded, cookies = seeded.run(build)
    assert 'spoofed' not in forwarded.values()
    assert not cookies
    if principal:
        if signed:
            claims = jwt.decode(
                forwarded[module.FORWARD_USER_INFO_HEADER_JWT], 'forwarding-test-secret', algorithms=['HS256']
            )
            assert claims['sub'] == principal
        else:
            assert forwarded[module.FORWARD_USER_INFO_HEADER_USER_ID] == principal
            assert forwarded[module.FORWARD_USER_INFO_HEADER_USER_ROLE] == 'user'
    else:
        assert not any(key.lower().startswith('x-openwebui-user-') for key in forwarded)
