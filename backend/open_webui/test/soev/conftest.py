"""Isolated signing configuration and recorded HTTP for identity and bootstrap."""

import importlib
import json

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


@pytest.fixture
def identity_config(monkeypatch, tmp_path):
    """Configure generated test keys without reading deployment credentials."""
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{tmp_path}/identity.db')
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    monkeypatch.setenv('VECTOR_DB', 'weaviate')
    for name in ('TYPE', 'USER', 'PASSWORD', 'HOST', 'PORT', 'NAME'):
        monkeypatch.setenv(f'DATABASE_{name}', '')
    identity = importlib.import_module('open_webui.soev.identity')
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    for name, value in {
        'URL': 'https://soev.invalid',
        'KEY': 'test-runtime-key',
        'CREDENTIAL_ID': 'runtime-credential',
        'SIGNING_KEY': pem,
        'SIGNING_KID': 'owui-test-key',
        'AUDIENCE': 'test-tenant',
        'SERVICE_PRINCIPAL': 'owui:service:webui',
    }.items():
        monkeypatch.setattr(identity.config, f'SOEV_API_{name}', value)
    monkeypatch.setattr(identity, '_linked_refs', set())
    return identity, key


@pytest.fixture
def identity_http(monkeypatch):
    """Record requests and allow asynchronous responses without network access."""
    requests, responses = [], []
    original_client = httpx.AsyncClient

    async def handle(request):
        requests.append(request)
        response = responses.pop(0)
        return await response(request) if callable(response) else response

    monkeypatch.setattr(
        'open_webui.soev.client.httpx.AsyncClient',
        lambda **kwargs: original_client(transport=httpx.MockTransport(handle), **kwargs),
    )
    return requests, responses


@pytest.fixture
def bootstrap_http(identity_http):
    """Model credential replay and signing-key creation, replay, and conflict."""
    requests, responses = identity_http
    credentials, signing_keys = {}, {}

    async def handle(request):
        body = json.loads(request.content)
        if request.url.path == '/v1/credentials':
            operation = request.headers['Idempotency-Key']
            if operation not in credentials:
                credentials[operation] = {'id': 'new-credential', 'secret': 'test-new-runtime-key', **body}
            return httpx.Response(201, json=credentials[operation])
        assert request.url.path == '/v1/credentials/new-credential/signing-keys'
        jwk = body['public_jwk']
        previous = signing_keys.get(jwk['kid'])
        if previous is not None and previous != jwk:
            return httpx.Response(409, json={})
        signing_keys[jwk['kid']] = jwk
        return httpx.Response(200 if previous else 201, json={'public_jwk': jwk})

    responses.extend([handle] * 4)
    return requests, credentials, signing_keys
