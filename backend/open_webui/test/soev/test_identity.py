"""OWUI identity and single-use Ed25519 assertion contracts."""

import asyncio
import base64
import datetime as dt
import json
import logging
import os
import subprocess
import sys
from unittest.mock import AsyncMock
from uuid import UUID, uuid5

import httpx
import pytest
from open_webui.soev.client import SoevApiError

NOW = dt.datetime(2026, 9, 11, 12, tzinfo=dt.UTC)


def decode_segment(segment):
    return base64.urlsafe_b64decode(segment + '=' * (-len(segment) % 4))


def claims(token):
    return json.loads(decode_segment(token.split('.')[1]))


@pytest.fixture
def user(identity_config):
    """Use the real user model with a local identity."""
    identity, _ = identity_config
    return identity.UserModel(
        id='alice', email='alice@example.invalid', name='Alice', last_active_at=0, updated_at=0, created_at=0
    )


def test_an_oauth_user_refs_its_owui_id(identity_config, user):
    """OAuth users keep the OWUI namespace under the revised B2 contract."""
    identity, _ = identity_config
    user.oauth = {'entra': {'sub': 'provider-sub'}}
    assert identity.external_ref(user) == 'owui:user:alice'


def test_a_local_user_refs_its_owui_id(identity_config, user):
    """Local users are named by their stable OWUI id."""
    identity, _ = identity_config
    assert identity.external_ref(user) == 'owui:user:alice'


@pytest.mark.asyncio
async def test_user_of_inverts_external_ref(identity_config, user, monkeypatch):
    """The inverse reads the Users model and preserves a missing user as None."""
    identity, _ = identity_config
    lookup = AsyncMock(side_effect=[user, None])
    monkeypatch.setattr(identity.Users, 'get_user_by_id', lookup)
    assert await identity.user_of(identity.external_ref(user)) == user
    assert await identity.user_of('owui:user:deleted') is None
    assert lookup.await_args_list[0].args == ('alice',)
    assert lookup.await_args_list[1].args == ('deleted',)
    for ref in ('entra:user:alice', 'owui:group:alice', 'owui:user:', 'owui:user:alice:extra'):
        assert await identity.user_of(ref) is None
    assert lookup.await_count == 2


def test_platform_user_id_is_stable_and_contains_no_colon(identity_config):
    """The fixed UUID namespace deterministically maps external refs to platform ids."""
    identity, _ = identity_config
    ref = 'owui:user:alice'
    result = identity.platform_user_id(ref)
    assert result == str(uuid5(identity.OWUI_PLATFORM_NAMESPACE, ref))
    assert UUID(result).version == 5
    assert ':' not in result
    assert identity.platform_user_id(ref) == result
    assert identity.platform_user_id('owui:user:bob') != result


def test_the_assertion_carries_iss_sub_aud_and_a_single_use_jti(identity_config):
    """Assertions name the credential and subject with a new UUID4 on each mint."""
    identity, _ = identity_config
    first, second = [identity.mint_assertion('owui:user:alice', now=NOW) for _ in range(2)]
    assert json.loads(decode_segment(first.split('.')[0])) == {'alg': 'Ed25519', 'kid': 'owui-test-key', 'typ': 'JWT'}
    payload = claims(first)
    assert payload == {
        'iss': 'runtime-credential',
        'sub': 'owui:user:alice',
        'aud': 'test-tenant',
        'iat': int(NOW.timestamp()),
        'exp': int(NOW.timestamp()) + 120,
        'jti': payload['jti'],
    }
    assert UUID(payload['jti']).version == 4
    assert payload['jti'] != claims(second)['jti']


def test_the_assertion_expires_within_five_minutes(identity_config):
    """The subject assertion expires exactly two minutes after its issue time."""
    identity, _ = identity_config
    payload = claims(identity.mint_assertion('owui:user:alice', now=NOW))
    assert payload['exp'] - payload['iat'] == 120
    assert payload['exp'] - payload['iat'] <= 300


def test_the_assertion_verifies_with_the_public_half(identity_config):
    """The public Ed25519 key verifies the compact JWS signing input directly."""
    identity, key = identity_config
    header, payload, signature = identity.mint_assertion('owui:user:alice', now=NOW).split('.')
    assert '=' not in header + payload + signature
    key.public_key().verify(decode_segment(signature), f'{header}.{payload}'.encode())


@pytest.mark.asyncio
async def test_ensure_link_is_idempotent(identity_config, identity_http):
    """A successful self-vouched link uses its body assertion and is cached in process."""
    identity, key = identity_config
    requests, responses = identity_http
    responses.append(httpx.Response(204))
    for _ in range(2):
        await identity.ensure_link('owui:user:alice', identity.build_client())
    assert len(requests) == 1
    request = requests[0]
    assert request.method == 'POST'
    assert request.url.path == '/v1/identity/links'
    assert request.headers['Idempotency-Key'] == 'link:owui:user:alice'
    assert request.headers['Authorization'] == 'Bearer test-runtime-key'
    assert 'X-Soev-Subject' not in request.headers
    body = json.loads(request.content)
    assert set(body) == {'platform_user_id', 'assertion'}
    assert body['platform_user_id'] == identity.platform_user_id('owui:user:alice')
    assert claims(body['assertion'])['sub'] == 'owui:user:alice'
    header, payload, signature = body['assertion'].split('.')
    key.public_key().verify(decode_segment(signature), f'{header}.{payload}'.encode())


@pytest.mark.asyncio
async def test_two_concurrent_first_logins_link_once(identity_config, identity_http):
    """Concurrent first logins wait for the same successful link."""
    identity, _ = identity_config
    requests, responses = identity_http
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed_response(_):
        entered.set()
        await release.wait()
        return httpx.Response(204)

    responses.append(delayed_response)
    first = asyncio.create_task(identity.ensure_link('owui:user:alice', identity.build_client()))
    await entered.wait()
    second = asyncio.create_task(identity.ensure_link('owui:user:alice', identity.build_client()))
    await asyncio.sleep(0)
    assert not second.done()
    release.set()
    await asyncio.gather(first, second)
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_a_failed_link_can_be_retried(identity_config, identity_http):
    """A failed link is never cached and the next attempt gets a fresh assertion."""
    identity, _ = identity_config
    requests, responses = identity_http
    responses.extend([httpx.Response(503, json={}), httpx.Response(204)])
    with pytest.raises(SoevApiError):
        await identity.ensure_link('owui:user:alice', identity.build_client())
    await identity.ensure_link('owui:user:alice', identity.build_client())
    assert len(requests) == 2
    assert json.loads(requests[0].content)['assertion'] != json.loads(requests[1].content)['assertion']
    assert requests[0].headers['Idempotency-Key'] == requests[1].headers['Idempotency-Key']


@pytest.mark.asyncio
async def test_acting_ref_links_before_each_request_mints_its_assertion(identity_config, identity_http, user):
    """Acting refs ensure the link while the built client signs every page separately."""
    identity, _ = identity_config
    requests, responses = identity_http
    responses.extend(
        [
            httpx.Response(204),
            httpx.Response(200, json={'data': [{'key': 'a'}], 'next_cursor': 'next'}),
            httpx.Response(200, json={'data': [{'key': 'b'}], 'next_cursor': None}),
        ]
    )
    client = identity.build_client()
    ref = await identity.acting_ref(user, client)
    assert ref == 'owui:user:alice'
    assert [item async for item in client.pages('/v1/collections', as_user=ref)] == [{'key': 'a'}, {'key': 'b'}]
    tokens = [json.loads(requests[0].content)['assertion']]
    tokens.extend(request.headers['X-Soev-Subject'] for request in requests[1:])
    assert len({claims(token)['jti'] for token in tokens}) == 3
    assert all(claims(token)['sub'] == ref for token in tokens)
    assert all(abs(claims(token)['iat'] - dt.datetime.now(dt.UTC).timestamp()) < 10 for token in tokens)


@pytest.mark.asyncio
async def test_the_private_key_never_reaches_a_log_record(identity_config, identity_http, caplog):
    """Signing and identity HTTP logs contain no PEM, API key, or subject assertion."""
    identity, _ = identity_config
    requests, responses = identity_http
    responses.append(httpx.Response(204))
    with caplog.at_level(logging.DEBUG):
        await identity.ensure_link('owui:user:alice', identity.build_client())
    exposed = caplog.text + repr([vars(record) for record in caplog.records])
    for secret in (
        identity.config.SOEV_API_SIGNING_KEY,
        'test-runtime-key',
        json.loads(requests[0].content)['assertion'],
    ):
        assert secret not in exposed


@pytest.mark.parametrize('credential_id', ['', 'deployment-credential'])
def test_the_credential_id_setting_is_deployment_only(tmp_path, credential_id):
    """The issuer setting comes from the environment without database registration."""
    environment = dict(os.environ, DATABASE_URL=f'sqlite:///{tmp_path}/config.db', VECTOR_DB='weaviate')
    for name in ('TYPE', 'USER', 'PASSWORD', 'HOST', 'PORT', 'NAME'):
        environment[f'DATABASE_{name}'] = ''
    environment['SOEV_API_CREDENTIAL_ID'] = credential_id
    code = (
        'import os; from open_webui import config; '
        'assert config.SOEV_API_CREDENTIAL_ID == os.environ["SOEV_API_CREDENTIAL_ID"]; '
        'assert not any(key.startswith("soev_api.") for key in config.DEFAULT_CONFIG)'
    )
    result = subprocess.run([sys.executable, '-c', code], env=environment, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
