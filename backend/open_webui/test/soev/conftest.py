"""Isolated signing configuration and recorded HTTP for identity assertions."""

import base64
import importlib

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
        'KEY': 'soev_test_cred-runtime_test-secret',
        'SIGNING_KEY': pem,
        'AUDIENCE': 'test-tenant',
        'SERVICE_PRINCIPAL': 'owui:service:webui',
    }.items():
        monkeypatch.setattr(identity.config, f'SOEV_API_{name}', value)
    monkeypatch.setattr(identity, '_linked_refs', set())
    monkeypatch.setattr(identity, '_pending_links', {})
    return identity, key


@pytest.fixture
def fake_api(identity_config, eligible_identity):
    """Register the declared credential and its public key under the JWK thumbprint."""
    from open_webui.test.soev.fake_api import FakeSoevApi

    identity, key = identity_config
    fake = FakeSoevApi()
    fake.credential_id = 'cred-runtime'
    fake.audience = identity.config.SOEV_API_AUDIENCE
    public_bytes = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    fake.signing_keys[identity.jwk_thumbprint(key)] = {
        'kty': 'OKP',
        'crv': 'Ed25519',
        'x': base64.urlsafe_b64encode(public_bytes).decode().rstrip('='),
    }
    return fake


@pytest.fixture
def identity_http(monkeypatch, eligible_identity):
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
def eligible_identity(identity_config, monkeypatch):
    """HTTP contract tests act as existing, approved fictional local accounts."""
    identity, _ = identity_config

    async def user_of(ref, *, db=None):
        return identity.UserModel(
            id=ref.removeprefix('owui:user:'),
            role='user',
            email='person@example.invalid',
            name='Person',
            last_active_at=0,
            updated_at=0,
            created_at=0,
        )

    monkeypatch.setattr(identity, 'user_of', user_of)
