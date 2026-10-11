"""Live sealed-stack checks; run only by runtime-security.yml."""

import pytest
from .client import (
    AttackClient,
    AuthenticationError,
    entered_the_handler,
    login,
)


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
    result = identities.user.request('POST', '/api/v1/chats/new', json={'chat': {'title': 'client-selftest'}})
    assert result.status_code == 200, result.text
    assert result.json()['user_id'] == identities.user.identity['id']
    assert entered_the_handler(result)
    deleted = identities.user.request('DELETE', f'/api/v1/chats/{result.json()["id"]}')
    assert deleted.status_code == 200, deleted.text


def test_wrong_password_is_a_loud_400(identities):
    from .identities import signin_alias

    user = identities.user
    with signin_alias(identities.admin, user.identity['id'], user.identity['email']) as email:
        with pytest.raises(AuthenticationError, match='HTTP 400'):
            login(email, 'wrong-password', base_url=user.base_url)


@pytest.mark.parametrize('challenge', ['requires_2fa', 'requires_2fa_setup'])
def test_real_2fa_signin_success_status_is_rejected(identities, challenge):
    import pyotp

    from .identities import PASSWORD, signin_alias

    admin, user = identities.admin, identities.intruder
    config_path = '/api/v1/configs/2fa'
    original = admin.request('GET', config_path)
    assert original.status_code == 200, original.text
    enrolled = False
    try:
        configured = admin.request(
            'POST',
            config_path,
            json={
                'ENABLE_2FA': True,
                'REQUIRE_2FA': challenge == 'requires_2fa_setup',
                'TWO_FA_GRACE_PERIOD_DAYS': 0,
            },
        )
        assert configured.status_code == 200, configured.text
        if challenge == 'requires_2fa':
            setup = user.request('POST', '/api/v1/auths/2fa/totp/setup')
            assert setup.status_code == 200, setup.text
            secret = setup.json()['secret']
            enabled = user.request(
                'POST',
                '/api/v1/auths/2fa/totp/enable',
                json={
                    'password': PASSWORD,
                    'secret': secret,
                    'code': pyotp.TOTP(secret).now(),
                },
            )
            assert enabled.status_code == 200, enabled.text
            enrolled = True
        with signin_alias(admin, user.identity['id'], user.identity['email']) as email, AttackClient() as anonymous:
            result = anonymous.request(
                'POST',
                '/api/v1/auths/signin',
                json={
                    'email': email,
                    'password': PASSWORD,
                },
            )
            assert result.status_code == 200, result.text
            assert result.json()[challenge] is True
            assert not entered_the_handler(result)
            with pytest.raises(AuthenticationError, match='2FA'):
                anonymous.authenticate(result, email)
            assert anonymous.token is None
            with pytest.raises(AuthenticationError, match='2FA'):
                login(email, PASSWORD, base_url=user.base_url)
    finally:
        try:
            if enrolled:
                disabled = user.request('POST', '/api/v1/auths/2fa/totp/disable', json={'password': PASSWORD})
                assert disabled.status_code == 200, disabled.text
        finally:
            restored = admin.request('POST', config_path, json=original.json())
            assert restored.status_code == 200, restored.text


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
        result = repaired.user.request('POST', '/api/v1/chats/new', json={'chat': {'title': 'repair-selftest'}})
        assert result.status_code == 200, result.text
        deleted = repaired.user.request('DELETE', f'/api/v1/chats/{result.json()["id"]}')
        assert deleted.status_code == 200, deleted.text
    finally:
        repaired.close()
