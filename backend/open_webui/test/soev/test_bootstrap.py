"""Recorded runtime credential and public signing-key bootstrap contracts."""

import base64
import importlib
import json
import logging
import os
import runpy

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from open_webui.soev.client import SoevApiError


@pytest.fixture
def bootstrap(identity_config, monkeypatch):
    """Supply the mint credential only to the bootstrap invocation."""
    monkeypatch.setenv('SOEV_BOOTSTRAP_MINT_KEY', 'test-mint-key')
    return importlib.import_module('open_webui.soev.bootstrap')


@pytest.mark.asyncio
async def test_bootstrap_mints_a_credential_without_mint(bootstrap, identity_config, bootstrap_http, capsys, caplog):
    """Bootstrap mints runtime authority and registers only its public Ed25519 material."""
    identity, key = identity_config
    requests, _, _ = bootstrap_http
    with caplog.at_level(logging.DEBUG):
        await bootstrap.bootstrap()
    assert len(requests) == 2
    assert all(request.method == 'POST' for request in requests)
    assert all(request.headers['Authorization'] == 'Bearer test-mint-key' for request in requests)
    assert all('X-Soev-Subject' not in request.headers for request in requests)
    assert json.loads(requests[0].content) == {
        'label': 'Open WebUI',
        'principal': 'owui:service:webui',
        'claimable_principals': ['owui:service:webui'],
        'capabilities': ['read', 'ingest', 'delete', 'directory'],
        'claimable_sources': ['owui'],
    }
    public_bytes = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    assert json.loads(requests[1].content) == {
        'public_jwk': {
            'kty': 'OKP',
            'crv': 'Ed25519',
            'x': base64.urlsafe_b64encode(public_bytes).decode().rstrip('='),
            'kid': 'owui-test-key',
            'alg': 'Ed25519',
            'use': 'sig',
        }
    }
    output = capsys.readouterr()
    assert json.loads(output.out) == {
        'SOEV_API_KEY': 'test-new-runtime-key',
        'SOEV_API_CREDENTIAL_ID': 'new-credential',
    }
    assert output.out.count('test-new-runtime-key') == 1
    exposed = caplog.text + repr([vars(record) for record in caplog.records])
    for secret in ('test-mint-key', 'test-new-runtime-key', identity.config.SOEV_API_SIGNING_KEY):
        assert secret not in exposed
        assert secret not in output.err
    assert 'test-mint-key' not in output.out
    assert identity.config.SOEV_API_KEY == 'test-runtime-key'
    assert not hasattr(identity.config, 'SOEV_BOOTSTRAP_MINT_KEY')


def test_bootstrap_rerun_changes_nothing(bootstrap, bootstrap_http, capsys, caplog):
    """A repeated invocation replays the mint and registers the identical kid and material."""
    requests, credentials, signing_keys = bootstrap_http
    bootstrap.main()
    capsys.readouterr()
    before = json.dumps([credentials, signing_keys], sort_keys=True)
    with caplog.at_level(logging.INFO):
        bootstrap.main()
    assert json.dumps([credentials, signing_keys], sort_keys=True) == before
    assert requests[0].headers['Idempotency-Key'] == requests[2].headers['Idempotency-Key']
    assert requests[1].headers['Idempotency-Key'] == requests[3].headers['Idempotency-Key']
    assert len(credentials) == len(signing_keys) == 1
    output = capsys.readouterr()
    assert json.loads(output.out) == {'SOEV_API_CREDENTIAL_ID': 'new-credential'}
    assert 'key was issued on the first run' in output.err
    assert 'test-new-runtime-key' not in output.out + output.err
    assert [record.status for record in caplog.records if record.name == 'open_webui.soev.client'] == [200, 200]


@pytest.mark.asyncio
async def test_bootstrap_refuses_a_conflicting_signing_key(
    bootstrap, identity_config, bootstrap_http, monkeypatch, capsys
):
    """A reused kid with different material fails without printing a runtime key."""
    identity, _ = identity_config
    await bootstrap.bootstrap()
    capsys.readouterr()
    replacement = (
        Ed25519PrivateKey.generate()
        .private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        .decode()
    )
    monkeypatch.setattr(identity.config, 'SOEV_API_SIGNING_KEY', replacement)
    with pytest.raises(SoevApiError) as caught:
        await bootstrap.bootstrap()
    assert caught.value.status == 409
    assert capsys.readouterr().out == ''


@pytest.mark.asyncio
async def test_bootstrap_requires_the_ephemeral_mint_key(bootstrap, identity_http, monkeypatch):
    """An absent mint credential fails before any HTTP request."""
    requests, _ = identity_http
    monkeypatch.delenv('SOEV_BOOTSTRAP_MINT_KEY')
    with pytest.raises(ValueError, match='SOEV_BOOTSTRAP_MINT_KEY'):
        await bootstrap.bootstrap()
    assert requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['secret', 'key'])
async def test_the_secret_field_is_secret(bootstrap, identity_http, field, capsys):
    """Only the contract's secret field supplies the plaintext credential."""
    _, responses = identity_http
    responses.extend(
        [
            httpx.Response(201, json={'id': 'new-credential', field: 'test-new-runtime-key'}),
            httpx.Response(201, json={'public_jwk': {}}),
        ]
    )
    if field == 'secret':
        await bootstrap.bootstrap()
        assert json.loads(capsys.readouterr().out)['SOEV_API_KEY'] == 'test-new-runtime-key'
    else:
        with pytest.raises(ValueError, match='credential id and plaintext key'):
            await bootstrap.bootstrap()
        assert capsys.readouterr().out == ''


def test_a_failed_key_registration_still_prints_the_new_secret(bootstrap, identity_http, capsys):
    """A registration failure leaves the freshly minted secret recoverable on stdout."""
    _, responses = identity_http

    async def refuse_registration(request):
        assert json.loads(capsys.readouterr().out) == {
            'SOEV_API_KEY': 'test-new-runtime-key',
            'SOEV_API_CREDENTIAL_ID': 'new-credential',
        }
        return httpx.Response(503, json={'detail': 'test-mint-key'})

    responses.extend(
        [
            httpx.Response(201, json={'id': 'new-credential', 'secret': 'test-new-runtime-key'}),
            refuse_registration,
        ]
    )
    with pytest.raises(SystemExit) as caught:
        bootstrap.main()
    assert caught.value.code == 1
    output = capsys.readouterr()
    assert 'credential was minted and printed' in output.err
    assert 'rerunning will register the key' in output.err
    assert 'test-mint-key' not in output.err


@pytest.mark.asyncio
async def test_bootstrap_validates_the_signing_key_before_minting(
    bootstrap, identity_config, identity_http, monkeypatch
):
    """Invalid signing configuration causes no credential creation."""
    identity, _ = identity_config
    requests, _ = identity_http
    monkeypatch.setattr(identity.config, 'SOEV_API_SIGNING_KEY', 'invalid-test-private-key')
    with pytest.raises(ValueError):
        await bootstrap.bootstrap()
    assert requests == []
    assert os.environ['SOEV_BOOTSTRAP_MINT_KEY'] == 'test-mint-key'


def test_the_module_entry_point_bootstraps(identity_config, bootstrap_http, monkeypatch, capsys):
    """Executing the module as __main__ completes both requests and prints the deployment values."""
    monkeypatch.setenv('SOEV_BOOTSTRAP_MINT_KEY', 'test-mint-key')
    runpy.run_module('open_webui.soev.bootstrap', run_name='__main__')
    assert json.loads(capsys.readouterr().out) == {
        'SOEV_API_KEY': 'test-new-runtime-key',
        'SOEV_API_CREDENTIAL_ID': 'new-credential',
    }
    assert len(bootstrap_http[0]) == 2


def test_the_entry_point_never_prints_an_upstream_error_body(bootstrap, identity_http, capsys):
    """Command failure reports only the HTTP status even if upstream reflects a secret."""
    _, responses = identity_http
    responses.append(
        httpx.Response(
            403,
            json={'code': 'scope_insufficient', 'detail': 'test-mint-key'},
            headers={'Content-Type': 'application/problem+json'},
        )
    )
    with pytest.raises(SystemExit) as caught:
        bootstrap.main()
    assert caught.value.code == 1
    output = capsys.readouterr()
    assert output.out == ''
    assert output.err == 'soev-api bootstrap failed (HTTP 403)\n'


@pytest.mark.asyncio
async def test_a_credential_without_its_secret_stops_bootstrap(bootstrap, identity_http, capsys):
    """An incomplete mint response cannot be mistaken for a usable runtime credential."""
    requests, responses = identity_http
    responses.append(httpx.Response(201, json={'id': 'new-credential'}))
    with pytest.raises(ValueError, match='credential id and plaintext key'):
        await bootstrap.bootstrap()
    assert len(requests) == 1
    assert capsys.readouterr().out == ''
