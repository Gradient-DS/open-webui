"""Isolated signing configuration and recorded HTTP for identity and bootstrap."""

import base64
import hashlib
import importlib
import json

import httpx
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from open_webui.test.soev.fake_api import FakeSoevApi


@pytest_asyncio.fixture(autouse=True)
async def shared_client_lifecycle():
    from open_webui.soev.client import close_client

    await close_client()
    yield
    await close_client()


@pytest.fixture
def chat_http(fake_api: FakeSoevApi, monkeypatch: pytest.MonkeyPatch) -> FakeSoevApi:
    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        'open_webui.soev.client.httpx.AsyncClient',
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(lambda request: fake_api.handle(request)), **kwargs
        ),
    )
    return fake_api


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
def fake_api(identity_config):
    """Register the configured public key as a completed bootstrap would."""
    from open_webui.test.soev.fake_api import FakeSoevApi

    identity, key = identity_config
    fake = FakeSoevApi()
    fake.credential_id = 'runtime-credential'
    fake.audience = identity.config.SOEV_API_AUDIENCE
    public_bytes = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    fake.signing_keys['owui-test-key'] = {
        'kty': 'OKP',
        'crv': 'Ed25519',
        'x': base64.urlsafe_b64encode(public_bytes).decode().rstrip('='),
    }
    return fake


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
                credential_id = 'new-credential' if not credentials else f'new-credential-{len(credentials) + 1}'
                credentials[operation] = {'id': credential_id, **body}
                return httpx.Response(201, json={**credentials[operation], 'secret': 'test-new-runtime-key'})
            return httpx.Response(200, json={**credentials[operation], 'secret': ''})
        credential = next(
            row for row in credentials.values() if request.url.path == f'/v1/credentials/{row["id"]}/signing-keys'
        )
        kid = hashlib.sha256(
            json.dumps([credential['principal'], credential['id'], request.headers['Idempotency-Key']]).encode()
        ).hexdigest()
        jwk = body['public_jwk']
        previous = signing_keys.get(kid)
        if previous is not None and previous != jwk:
            return httpx.Response(409, json={'code': 'idempotency_key_reused'})
        signing_keys[kid] = jwk
        return httpx.Response(
            200 if previous is not None else 201,
            json={'kid': kid, 'public_jwk': jwk, 'created_at': '2026-09-16T12:00:00Z', 'retired_at': None},
        )

    responses.extend([handle] * 4)
    return requests, credentials, signing_keys
