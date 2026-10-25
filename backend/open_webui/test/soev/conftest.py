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
    monkeypatch.setattr(identity.config, 'SOEV_ARCHIVE_READERS_GROUP_REF', 'platform:group:archive-readers')
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


@pytest_asyncio.fixture
async def role_store(identity_config, monkeypatch, platform_capabilities):
    from types import SimpleNamespace

    from open_webui.internal import db as database
    from open_webui.internal.db import Base
    from open_webui.models.users import User, Users
    from open_webui.soev import identity
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    monkeypatch.setattr(database, 'DATABASE_ENABLE_SESSION_SHARING', True)
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[User.__table__]))
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        await Users.insert_new_user('fern', 'Fern', 'fern@example.invalid', db=db)
        yield SimpleNamespace(db=db, users=Users, identity=identity)
    await engine.dispose()


@pytest_asyncio.fixture
async def group_db(identity_config, monkeypatch):
    from open_webui.internal import db as database
    from open_webui.models.access_grants import AccessGrant
    from open_webui.models.groups import Group, GroupMember
    from open_webui.models.users import User
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    monkeypatch.setattr(database, 'DATABASE_ENABLE_SESSION_SHARING', True)
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')

    @event.listens_for(engine.sync_engine, 'connect')
    def enable_foreign_keys(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')

    async with engine.begin() as conn:
        await conn.run_sync(
            lambda db: database.Base.metadata.create_all(
                db, tables=[Group.__table__, GroupMember.__table__, User.__table__, AccessGrant.__table__]
            )
        )
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        db.add_all(
            [
                Group(
                    id='old',
                    user_id='fern',
                    name='Old',
                    description='',
                    created_at=1,
                    updated_at=1,
                    permissions={'workspace': {'knowledge': True}},
                    data={'config': {'share': 'members'}},
                ),
                User(
                    id='fern',
                    name='Fern',
                    email='fern@example.invalid',
                    role='admin',
                    person_id='platform:user:fern-person',
                    last_active_at=1,
                    created_at=1,
                    updated_at=1,
                ),
                User(
                    id='lark',
                    name='Lark',
                    email='lark@example.invalid',
                    role='user',
                    person_id='platform:user:lark-person',
                    last_active_at=1,
                    created_at=1,
                    updated_at=1,
                ),
                User(
                    id='unlinked',
                    name='Reed',
                    email='reed@example.invalid',
                    role='admin',
                    last_active_at=1,
                    created_at=1,
                    updated_at=1,
                ),
            ]
        )
        await db.flush()
        db.add(GroupMember(id='membership', group_id='old', user_id='fern', created_at=1, updated_at=1))
        await db.commit()
        yield db
    await engine.dispose()
