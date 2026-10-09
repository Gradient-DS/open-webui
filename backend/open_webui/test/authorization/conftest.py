"""Seeded SQLite with real authentication and request-scoped database sessions."""

import asyncio
import shutil
import socket
import sys
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

PRINCIPALS = ('admin', 'owner', 'reader', 'writer', 'outsider', 'other_group', 'public')
MODULES = (
    'chats',
    'files',
    'notes',
    'folders',
    'models',
    'prompts',
    'tools',
    'channels',
    'knowledge',
    'groups',
    'users',
)


@pytest.fixture(scope='module')
def no_network():
    connect = socket.socket.connect
    attempts = []

    def local_only(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            attempts.append(address)
            raise AssertionError(f'Unexpected network connection: {address}')
        return connect(sock, address)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(socket.socket, 'connect', local_only)
        yield
    assert not attempts, f'Network attempts, including swallowed exceptions: {attempts}'


@pytest.fixture(scope='module')
def application(tmp_path_factory, no_network):
    with pytest.MonkeyPatch.context() as patch:
        directory = tmp_path_factory.mktemp('acl-import')
        for key, value in {
            'DATABASE_URL': f'sqlite:///{directory}/import.db',
            'DATA_DIR': str(directory),
            'ENABLE_DB_MIGRATIONS': 'false',
            'VECTOR_DB': 'weaviate',
            'OFFLINE_MODE': 'true',
            'OLLAMA_BASE_URL': 'http://ollama.invalid',
        }.items():
            patch.setenv(key, value)
        for part in ('TYPE', 'USER', 'PASSWORD', 'HOST', 'PORT', 'NAME'):
            patch.setenv(f'DATABASE_{part}', '')
        from open_webui.retrieval.vector.dbs.weaviate_multitenancy import WeaviateClient

        # Only external vector I/O is replaced; SQL and authorization stay real.
        patch.setattr(WeaviateClient, '__init__', lambda self: None)
        from open_webui.main import app

    return app


@pytest.fixture(scope='module')
def template(application, tmp_path_factory):
    from open_webui.internal.db import Base
    from open_webui.models.config import Config
    from open_webui.models.groups import Group, GroupMember
    from open_webui.models.users import User
    from open_webui.config import DEFAULT_USER_PERMISSIONS
    from copy import deepcopy

    path = tmp_path_factory.mktemp('acl-template') / 'seed.db'
    engine = create_engine(f'sqlite:///{path}')
    Base.metadata.create_all(engine)
    permissions = deepcopy(DEFAULT_USER_PERMISSIONS)
    for category in ('workspace', 'sharing', 'features', 'chat', 'access_grants'):
        permissions[category] = {key: True for key in permissions[category]}
    with sessionmaker(engine)() as db:
        for principal in (*PRINCIPALS, 'pending'):
            db.add(
                User(
                    id=principal,
                    name=principal,
                    email=f'{principal}@acl.invalid',
                    role=principal if principal in ('admin', 'pending') else 'user',
                    created_at=1,
                    updated_at=1,
                    last_active_at=1,
                    variables={},
                )
            )
        for group, member in (('readers', 'reader'), ('writers', 'writer'), ('unrelated', 'other_group')):
            db.add(
                Group(
                    id=group, user_id='owner', name=group, description=group, permissions={}, created_at=1, updated_at=1
                )
            )
            db.add(GroupMember(id=group, group_id=group, user_id=member, created_at=1, updated_at=1))
        db.add_all(
            [
                Config(key='user.permissions', value=permissions),
                Config(key='channels.enable', value=True),
                Config(key='folders.enable', value=True),
            ]
        )
        db.commit()
    engine.dispose()
    return path


@pytest.fixture
def seeded(template, application, monkeypatch, tmp_path):
    from open_webui.internal import db as database
    from open_webui.models.config import Config
    from open_webui.utils import features
    from open_webui.utils.auth import create_token
    from open_webui.models.access_grants import AccessGrants, AccessGrantsTable
    from open_webui.models.groups import Groups, GroupTable
    from open_webui.models.knowledge import Knowledges, KnowledgeTable

    # The fork exports remote adapters unconditionally; exercise the retained SQL implementations.
    sql_grants, sql_knowledge, sql_groups = AccessGrantsTable(), KnowledgeTable(), GroupTable()
    for name, module in list(sys.modules.items()):
        if name.startswith('open_webui.') and module is not None:
            if getattr(module, 'AccessGrants', None) is AccessGrants:
                monkeypatch.setattr(module, 'AccessGrants', sql_grants)
            if getattr(module, 'Knowledges', None) is Knowledges:
                monkeypatch.setattr(module, 'Knowledges', sql_knowledge)
            if getattr(module, 'Groups', None) is Groups:
                monkeypatch.setattr(module, 'Groups', sql_groups)

    path = tmp_path / 'seed.db'
    shutil.copyfile(template, path)
    engine = create_engine(f'sqlite:///{path}')
    async_engine = create_async_engine(f'sqlite+aiosqlite:///{path}', poolclass=NullPool)
    sessions = async_sessionmaker(async_engine, expire_on_commit=False)
    monkeypatch.setattr(database, 'AsyncSessionLocal', sessions)
    monkeypatch.setattr(database, 'SessionLocal', sessionmaker(engine, expire_on_commit=False))
    monkeypatch.setattr(Config, 'PERSISTENT_ENABLED', True)
    for feature in ('knowledge', 'models', 'prompts', 'tools'):
        monkeypatch.setitem(features.FEATURE_FLAGS, feature, True)

    prefixes = tuple(f'/api/v1/{name}/' for name in MODULES)
    app = FastAPI(routes=[route for route in application.routes if route.path.startswith(prefixes)])
    app.state.redis = None
    app.state.MODELS = {}
    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client,
            run=client.portal.call,
            sessions=sessions,
            engine=engine,
            headers=lambda principal: {'Authorization': f'Bearer {create_token({"id": principal})}'},
        )
        client.portal.call(drain_background_tasks)
    asyncio.run(async_engine.dispose())
    engine.dispose()


async def drain_background_tasks():
    while pending := [
        task
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task() and '/open_webui/' in task.get_coro().cr_code.co_filename
    ]:
        await asyncio.gather(*pending)
