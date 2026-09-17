"""Recorded runtime credential and public signing-key bootstrap contracts."""

import base64
import hashlib
import importlib
import json
import logging
import os
import runpy

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


@pytest.fixture
def bootstrap(identity_config, monkeypatch):
    """Supply the mint credential only to the bootstrap invocation."""
    monkeypatch.setenv('SOEV_BOOTSTRAP_MINT_KEY', 'test-mint-key')
    return importlib.import_module('open_webui.soev.bootstrap')


@pytest.mark.asyncio
async def test_bootstrap_prints_the_api_assigned_kid(
    bootstrap, identity_config, bootstrap_http, capsys, caplog, monkeypatch
):
    """Bootstrap mints runtime authority and registers only its public Ed25519 material."""
    identity, key = identity_config
    requests, _, signing_keys = bootstrap_http
    monkeypatch.setattr(identity.config, 'SOEV_API_SIGNING_KID', '')
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
        'capabilities': ['read', 'ingest', 'delete', 'directory', 'connect'],
        'claimable_sources': ['owui'],
    }
    public_bytes = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    assert json.loads(requests[1].content) == {
        'public_jwk': {
            'kty': 'OKP',
            'crv': 'Ed25519',
            'x': base64.urlsafe_b64encode(public_bytes).decode().rstrip('='),
            'alg': 'Ed25519',
            'use': 'sig',
        }
    }
    output = capsys.readouterr()
    kid = hashlib.sha256(
        json.dumps(['owui:service:webui', 'new-credential', requests[1].headers['Idempotency-Key']]).encode()
    ).hexdigest()
    assert list(signing_keys) == [kid]
    assert [json.loads(line) for line in output.out.splitlines()] == [
        {'SOEV_API_KEY': 'test-new-runtime-key', 'SOEV_API_CREDENTIAL_ID': 'new-credential'},
        {'SOEV_API_CREDENTIAL_ID': 'new-credential', 'SOEV_API_SIGNING_KID': kid},
    ]
    assert output.out.count('test-new-runtime-key') == 1
    exposed = caplog.text + repr([vars(record) for record in caplog.records])
    for secret in ('test-mint-key', 'test-new-runtime-key', identity.config.SOEV_API_SIGNING_KEY):
        assert secret not in exposed
        assert secret not in output.err
    assert 'test-mint-key' not in output.out
    assert identity.config.SOEV_API_KEY == 'test-runtime-key'
    assert not hasattr(identity.config, 'SOEV_BOOTSTRAP_MINT_KEY')


def test_bootstrap_rerun_prints_the_same_kid_and_mints_nothing(
    bootstrap, identity_config, bootstrap_http, capsys, caplog, monkeypatch
):
    """A repeated invocation replays the mint and registers the identical kid and material."""
    requests, credentials, signing_keys = bootstrap_http
    bootstrap.main()
    first_output = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    kid = first_output[-1]['SOEV_API_SIGNING_KID']
    identity, _ = identity_config
    monkeypatch.setattr(identity.config, 'SOEV_API_SIGNING_KID', kid)
    before = json.dumps([credentials, signing_keys], sort_keys=True)
    with caplog.at_level(logging.INFO):
        bootstrap.main()
    assert json.dumps([credentials, signing_keys], sort_keys=True) == before
    assert requests[0].headers['Idempotency-Key'] == requests[2].headers['Idempotency-Key']
    assert requests[1].headers['Idempotency-Key'] == requests[3].headers['Idempotency-Key']
    assert len(credentials) == len(signing_keys) == 1
    output = capsys.readouterr()
    assert [json.loads(line) for line in output.out.splitlines()] == [
        {'SOEV_API_CREDENTIAL_ID': 'new-credential', 'SOEV_API_SIGNING_KID': kid}
    ]
    assert 'key was issued on the first run' in output.err
    assert 'test-new-runtime-key' not in output.out + output.err
    assert [record.status for record in caplog.records if record.name == 'open_webui.soev.client'] == [200, 200]


@pytest.mark.asyncio
async def test_a_changed_signing_key_mints_a_new_credential(
    bootstrap, identity_config, bootstrap_http, monkeypatch, capsys
):
    """New signing material starts a distinct credential and signing-key registration."""
    identity, _ = identity_config
    requests, credentials, signing_keys = bootstrap_http
    await bootstrap.bootstrap()
    first_output = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    replacement = (
        Ed25519PrivateKey.generate()
        .private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        .decode()
    )
    monkeypatch.setattr(identity.config, 'SOEV_API_SIGNING_KEY', replacement)
    await bootstrap.bootstrap()
    output = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(credentials) == len(signing_keys) == 2
    assert output[0]['SOEV_API_KEY'] == 'test-new-runtime-key'
    assert output[-1]['SOEV_API_CREDENTIAL_ID'] == 'new-credential-2'
    assert output[-1]['SOEV_API_SIGNING_KID'] in signing_keys
    assert output[-1]['SOEV_API_SIGNING_KID'] != first_output[-1]['SOEV_API_SIGNING_KID']
    assert requests[0].headers['Idempotency-Key'] != requests[2].headers['Idempotency-Key']
    assert requests[1].headers['Idempotency-Key'] != requests[3].headers['Idempotency-Key']


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
            httpx.Response(201, json={'kid': 'api-assigned-kid', 'public_jwk': {}}),
        ]
    )
    if field == 'secret':
        await bootstrap.bootstrap()
        assert json.loads(capsys.readouterr().out.splitlines()[0])['SOEV_API_KEY'] == 'test-new-runtime-key'
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
    assert [json.loads(line) for line in capsys.readouterr().out.splitlines()] == [
        {'SOEV_API_KEY': 'test-new-runtime-key', 'SOEV_API_CREDENTIAL_ID': 'new-credential'},
        {'SOEV_API_CREDENTIAL_ID': 'new-credential', 'SOEV_API_SIGNING_KID': next(iter(bootstrap_http[2]))},
    ]
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


@pytest.mark.parametrize('registration', [{}, {'kid': None}, {'kid': 123}])
def test_a_registration_without_a_string_kid_fails_after_printing_the_secret(
    bootstrap, identity_http, capsys, registration
):
    """Malformed registration responses preserve the minted secret and fail the command."""
    _, responses = identity_http
    responses.extend(
        [
            httpx.Response(201, json={'id': 'new-credential', 'secret': 'test-new-runtime-key'}),
            httpx.Response(201, json=registration),
        ]
    )
    with pytest.raises(SystemExit) as caught:
        bootstrap.main()
    assert caught.value.code == 1
    output = capsys.readouterr()
    assert json.loads(output.out) == {
        'SOEV_API_KEY': 'test-new-runtime-key',
        'SOEV_API_CREDENTIAL_ID': 'new-credential',
    }
    assert 'signing-key registration did not return a string kid' in output.err
