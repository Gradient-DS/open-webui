"""Offboarding content guards, person bindings and dependent-row isolation."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from open_webui.internal.db import Base
from open_webui.models.chat_messages import ChatMessage
from open_webui.models.chats import Chat, ChatFile
from open_webui.models.config import Config
from open_webui.models.feedbacks import Feedback
from open_webui.models.files import File
from open_webui.models.knowledge import Knowledge, KnowledgeFile
from open_webui.models.users import User
from open_webui.services.deletion import platform_offboarding as local
from open_webui.soev import offboarding, threads
from open_webui.test.soev.fake_api import person_ref
from open_webui.test.soev.test_platform_offboarding import OTHER, SOURCE, TARGET, task
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['delete', 'transfer', 'archive'])
async def test_prepared_threads_refuse_before_receipt_or_external_effects(monkeypatch, mode):
    monkeypatch.setattr(threads, 'enabled', lambda: True)
    prepare = AsyncMock(side_effect=AssertionError('Receipt preparation must not run'))
    external = AsyncMock(side_effect=AssertionError('External disposal must not run'))
    monkeypatch.setattr(local, '_prepare', prepare)
    monkeypatch.setattr(local, '_external', external)
    assert await local.apply(task(mode)) == 'unsupported_content'
    prepare.assert_not_awaited()
    external.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['delete', 'transfer', 'archive'])
async def test_pull_acknowledges_prepared_thread_refusal(monkeypatch, mode):
    work = task(mode)
    remote = SimpleNamespace(get=AsyncMock(return_value=[work.model_dump(mode='json')]), send=AsyncMock())
    monkeypatch.setattr(threads, 'enabled', lambda: True)
    monkeypatch.setattr(offboarding, 'v2_enabled', lambda: True)
    monkeypatch.setattr(offboarding, 'build_service_client', lambda: remote)
    await offboarding.poll()
    remote.send.assert_awaited_once_with(
        'POST',
        '/v1/offboarding/fictional-job/acknowledgement',
        {'error': 'unsupported_content'},
        idempotency_key='offboarding-ack:fictional-job',
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'name,args',
    [
        ('_archive_organisation', (SOURCE, 'fictional-job')),
        ('_content_conflict', (SOURCE, False)),
        ('_dependents', (SOURCE, File, ['fictional-file'])),
        ('_local', (SOURCE, TARGET, task('transfer'))),
        ('_organisation_grants', (SOURCE, False)),
        ('_unsupported_content', (SOURCE, False)),
    ],
)
async def test_direct_content_entrypoints_refuse_before_sql(monkeypatch, name, args):
    monkeypatch.setattr(threads, 'enabled', lambda: True)
    db = AsyncMock(spec=AsyncSession)
    with pytest.raises(threads.ThreadError) as error:
        await getattr(local, name)(db, *args)
    assert (error.value.status, error.value.state) == (409, 'unavailable')
    assert db.mock_calls == []


@pytest_asyncio.fixture
async def content_store(monkeypatch):
    monkeypatch.setattr(threads, 'enabled', lambda: False)
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')

    @event.listens_for(engine.sync_engine, 'connect')
    def foreign_keys(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def database():
        async with sessions() as db:
            yield db

    external = AsyncMock()
    monkeypatch.setattr(local, 'get_async_db', database)
    monkeypatch.setattr(local, '_external', external)
    async with sessions() as db:
        for id, name in ((SOURCE, 'cobalt'), (TARGET, 'quartz'), (OTHER, 'jasper')):
            db.add(
                User(
                    id=id,
                    name='Fictional ' + name,
                    email=name + '@example.invalid',
                    role='user',
                    person_id=person_ref(name),
                )
            )
        for owner in (SOURCE, OTHER):
            db.add(File(id='file-' + owner, user_id=owner, filename='fictional.txt', path=None, meta={}))
            db.add(Chat(id='chat-' + owner, user_id=owner, title='Fictional chat', chat={}, created_at=1, updated_at=1))
            db.add(Feedback(id='feedback-' + owner, user_id=owner, type='rating', data={'rating': 1}))
        await db.flush()
        for owner in (SOURCE, OTHER):
            db.add(
                ChatFile(
                    id='chat-file-' + owner,
                    user_id=owner,
                    chat_id='chat-' + owner,
                    file_id='file-' + owner,
                    created_at=1,
                    updated_at=1,
                )
            )
            db.add(
                ChatMessage(
                    id='message-' + owner,
                    user_id=owner,
                    chat_id='chat-' + owner,
                    role='user',
                    content='Fictional text',
                )
            )
        await db.commit()
    try:
        yield SimpleNamespace(sessions=sessions, external=external)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['delete', 'transfer', 'archive'])
async def test_owned_content_disposition_preserves_other_people(content_store, mode):
    assert await local.apply(task(mode)) is None
    content_store.external.assert_awaited_once()
    assert content_store.external.await_args.args[1:] == (SOURCE, TARGET if mode == 'transfer' else None)
    async with content_store.sessions() as db:
        assert await db.get(User, SOURCE) is None
        assert (await db.get(User, OTHER)).person_id == person_ref('jasper')
        assert (await db.get(Chat, 'chat-' + OTHER)).user_id == OTHER
        assert (await db.get(File, 'file-' + OTHER)).user_id == OTHER
        assert (await db.get(Feedback, 'feedback-' + OTHER)).data == {'rating': 1}
        assert (await db.get(ChatFile, 'chat-file-' + OTHER)).user_id == OTHER
        assert (await db.get(ChatMessage, 'message-' + OTHER)).content == 'Fictional text'
        assert await db.get(Feedback, 'feedback-' + SOURCE) is None
        for table, prefix in ((ChatFile, 'chat-file-'), (ChatMessage, 'message-')):
            row = await db.get(table, prefix + SOURCE)
            if mode == 'transfer':
                assert row.user_id == TARGET
            else:
                assert row is None
        own = await db.get(Chat, 'chat-' + SOURCE)
        if mode == 'delete':
            assert own is None
        else:
            assert own.user_id == (TARGET if mode == 'transfer' else 'platform:svc:archive-offboarding-fictional-job')
        assert (await db.get(Config, local._key(task(mode).person))).value['state'] == 'completed'


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['delete', 'transfer', 'archive'])
@pytest.mark.parametrize('dependency', ['chat-file', 'chat-message', 'file-link', 'knowledge-file'])
async def test_foreign_content_dependencies_refuse_before_any_effect(content_store, dependency, mode):
    async with content_store.sessions() as db:
        if dependency == 'chat-message':
            db.add(
                ChatMessage(
                    id='fictional-foreign',
                    user_id=OTHER,
                    chat_id='chat-' + SOURCE,
                    role='user',
                    content='Fictional text',
                )
            )
        elif dependency == 'knowledge-file':
            db.add(Knowledge(id='fictional-knowledge', user_id=OTHER, type='local', name='Fictional library'))
            await db.flush()
            db.add(
                KnowledgeFile(
                    id='fictional-foreign',
                    user_id=OTHER,
                    knowledge_id='fictional-knowledge',
                    file_id='file-' + SOURCE,
                    created_at=1,
                    updated_at=1,
                )
            )
        else:
            db.add(
                ChatFile(
                    id='fictional-foreign',
                    user_id=OTHER,
                    chat_id='chat-' + (SOURCE if dependency == 'chat-file' else OTHER),
                    file_id='file-' + (OTHER if dependency == 'chat-file' else SOURCE),
                    created_at=1,
                    updated_at=1,
                )
            )
        (await db.get(File, 'file-' + SOURCE)).meta = {'source': 'fictional-drive'}
        await db.commit()
    assert await local.apply(task(mode)) == 'unsupported_content'
    content_store.external.assert_not_awaited()
    async with content_store.sessions() as db:
        assert (await db.get(User, SOURCE)).role == 'user'
        assert (await db.get(Chat, 'chat-' + SOURCE)).user_id == SOURCE
        assert await db.get(File, 'file-' + SOURCE) is not None
        assert await db.get(Config, local._key(task(mode).person)) is None
        table = (
            ChatMessage
            if dependency == 'chat-message'
            else KnowledgeFile
            if dependency == 'knowledge-file'
            else ChatFile
        )
        assert (await db.get(table, 'fictional-foreign')).user_id == OTHER


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['delete', 'transfer', 'archive'])
async def test_prepared_receipt_rechecks_foreign_dependencies(content_store, mode):
    work = task(mode)
    assert await local._prepare(work) is None
    async with content_store.sessions() as db:
        db.add(
            ChatMessage(
                id='fictional-late-message',
                user_id=OTHER,
                chat_id='chat-' + SOURCE,
                role='user',
                content='Fictional text',
            )
        )
        await db.commit()
    assert await local.apply(work) == 'unsupported_content'
    content_store.external.assert_not_awaited()
    async with content_store.sessions() as db:
        assert (await db.get(ChatMessage, 'fictional-late-message')).user_id == OTHER
        assert (await db.get(Chat, 'chat-' + SOURCE)).user_id == SOURCE
        assert (await db.get(Config, local._key(work.person))).value['state'] == 'prepared'


@pytest.mark.asyncio
async def test_file_dependency_cleanup_preserves_foreign_links(content_store):
    async with content_store.sessions() as db:
        db.add(
            ChatFile(
                id='fictional-foreign-link',
                user_id=OTHER,
                chat_id='chat-' + OTHER,
                file_id='file-' + SOURCE,
                created_at=1,
                updated_at=1,
            )
        )
        await db.commit()
        await local._dependents(db, SOURCE, File, ['file-' + SOURCE])
        await db.commit()
    async with content_store.sessions() as db:
        assert await db.get(ChatFile, 'chat-file-' + SOURCE) is None
        assert (await db.get(ChatFile, 'fictional-foreign-link')).user_id == OTHER
        assert (await db.get(ChatFile, 'chat-file-' + OTHER)).user_id == OTHER


@pytest.mark.asyncio
@pytest.mark.parametrize('binding', ['source', 'target'])
async def test_receipt_binding_changes_refuse_before_effects(content_store, binding):
    work = task('transfer')
    assert await local._prepare(work) is None
    async with content_store.sessions() as db:
        (await db.get(User, SOURCE if binding == 'source' else TARGET)).person_id = person_ref('fictional-replacement')
        await db.commit()
    assert await local.apply(work) == ('source_conflict' if binding == 'source' else 'target_missing')
    content_store.external.assert_not_awaited()
    async with content_store.sessions() as db:
        assert (await db.get(Chat, 'chat-' + SOURCE)).user_id == SOURCE
        assert (await db.get(Config, local._key(work.person))).value['state'] == 'prepared'
