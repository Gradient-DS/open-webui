"""Live sealed-stack checks; run only by runtime-security.yml."""

from uuid import uuid4

import pytest

from . import oidc
from .client import (
    AttackClient,
    AuthenticationError,
    entered_the_handler,
    login,
)
from .seeds import runtime_soev_backed


def new_chat_body(title):
    if runtime_soev_backed() == 'drive':
        return {'agent': 'soev_react', 'input': {'text': title}}
    return {'chat': {'title': title}}


def test_an_unauthenticated_client_is_refused():
    with AttackClient() as client:
        result = client.request('GET', '/api/v1/auths/')
        assert result.status_code == 401, result.text
        assert not entered_the_handler(result)


@pytest.fixture
def identities():
    from .identities import ensure_identities

    result = ensure_identities()
    yield result
    result.close()


def test_signup_mints_admin_and_distinct_ordinary_identities(identities):
    assert identities.admin.identity['role'] == 'admin'
    assert identities.user.identity['role'] == identities.intruder.identity['role'] == 'user'
    assert len({c.identity['id'] for c in identities.clients}) == 3
    result = identities.admin.request('GET', '/api/v1/users/')
    assert result.status_code == 200, result.text


def test_an_authenticated_client_can_write(identities):
    result = identities.user.request('POST', '/api/v1/chats/new', json=new_chat_body('client-selftest'))
    assert result.status_code == 200, result.text
    if runtime_soev_backed() == 'drive':
        own = identities.user.request('GET', f'/api/v1/chats/{result.json()["id"]}')
        assert own.status_code == 200, own.text
        foreign = identities.intruder.request('GET', f'/api/v1/chats/{result.json()["id"]}')
        assert foreign.status_code == 404, foreign.text
    else:
        assert result.json()['user_id'] == identities.user.identity['id']
    assert entered_the_handler(result)
    deleted = identities.user.request('DELETE', f'/api/v1/chats/{result.json()["id"]}')
    assert deleted.status_code == 200, deleted.text


def test_live_repair_preserves_ids_after_throttle_password_change_and_signout(identities):
    from .identities import PASSWORD, ensure_identities

    ids = [c.identity['id'] for c in identities.clients]
    user = identities.user
    changed = user.request(
        'POST',
        '/api/v1/auths/update/password',
        json={
            'password': PASSWORD,
            'new_password': 'Hostile-Replacement-Passw0rd!',
        },
    )
    assert changed.status_code == 200 and changed.json() is True
    with AttackClient() as anonymous:
        for _ in range(20):
            result = anonymous.request(
                'POST',
                '/api/v1/auths/signin',
                json={
                    'email': user.identity['email'],
                    'password': 'wrong-password',
                },
            )
            if result.status_code == 429:
                break
        else:
            pytest.fail('Never reached the real signin throttle')
        assert not entered_the_handler(result)
    signed_out = identities.admin.request('POST', '/api/v1/auths/signout')
    assert signed_out.status_code == 200, signed_out.text
    repaired = ensure_identities()
    try:
        assert [c.identity['id'] for c in repaired.clients] == ids
        result = repaired.user.request('POST', '/api/v1/chats/new', json=new_chat_body('repair-selftest'))
        assert result.status_code == 200, result.text
        deleted = repaired.user.request('DELETE', f'/api/v1/chats/{result.json()["id"]}')
        assert deleted.status_code == 200, deleted.text
    finally:
        repaired.close()


def test_wrong_password_is_a_loud_400(identities):
    user = identities.user
    with pytest.MonkeyPatch.context() as password_mode:
        password_mode.delenv('ATTACK_OIDC_LOGIN', raising=False)
        with pytest.raises(AuthenticationError, match='HTTP 400'):
            login(user.identity['email'], 'wrong-password', base_url=user.base_url)
