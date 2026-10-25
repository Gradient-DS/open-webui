"""Isolated product configuration and recorded HTTP for login-token calls."""

import importlib

import httpx
import pytest
import pytest_asyncio
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
    """Configure fictional product credentials."""
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{tmp_path}/identity.db')
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    monkeypatch.setenv('VECTOR_DB', 'weaviate')
    for name in ('TYPE', 'USER', 'PASSWORD', 'HOST', 'PORT', 'NAME'):
        monkeypatch.setenv(f'DATABASE_{name}', '')
    identity = importlib.import_module('open_webui.soev.identity')
    for name, value in {
        'URL': 'https://soev.invalid',
        'KEY': 'soev_test_cred-runtime_test-secret',
        'SERVICE_PRINCIPAL': 'owui:service:webui',
    }.items():
        monkeypatch.setattr(identity.config, f'SOEV_API_{name}', value)
    return identity, None


@pytest.fixture
def fake_api(identity_config, eligible_identity, login_tokens):
    """Use the login-token-only platform contract."""
    from open_webui.test.soev.fake_api import FakeSoevApi

    fake = FakeSoevApi()
    return fake


@pytest.fixture
def identity_http(monkeypatch, eligible_identity, login_tokens):
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


@pytest_asyncio.fixture
async def role_store(identity_config, monkeypatch, platform_capabilities):
    from types import SimpleNamespace

    from open_webui.internal.db import Base
    from open_webui.models.users import User, Users
    from open_webui.soev import group_sync, identity
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    monkeypatch.setattr(group_sync, '_pending', {})

    from open_webui.internal import db as database

    monkeypatch.setattr(database, 'DATABASE_ENABLE_SESSION_SHARING', True)
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[User.__table__]))
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        await Users.insert_new_user('fern', 'Fern', 'fern@example.invalid', db=db)
        yield SimpleNamespace(db=db, users=Users, identity=identity)
    await engine.dispose()


@pytest.fixture
def login_tokens(monkeypatch):
    """Contract transports stand in for verified logins; request-context tests use the real resolver."""
    from open_webui.soev import acting, client

    async def resolve(ref):
        if acting._token_resolver.get() is not None:
            return await acting.user_token(ref)
        return f'fictional-login:{ref}' if ref else None

    monkeypatch.setattr(client, 'user_token', resolve)
    return resolve
