"""Platform participant effects and receipts over isolated PostgreSQL product tables."""

import asyncio
import os
from collections import Counter
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from open_webui.internal.db import Base
from open_webui.models.access_grants import AccessGrant
from open_webui.models.auths import Auth
from open_webui.models.calendar import Calendar, CalendarEvent, CalendarEventAttendee
from open_webui.models.chats import Chat
from open_webui.models.files import File
from open_webui.models.folders import Folder
from open_webui.models.functions import Function
from open_webui.models.knowledge import Knowledge
from open_webui.models.memories import Memory
from open_webui.models.models import Model
from open_webui.models.notes import Note
from open_webui.models.prompts import Prompt
from open_webui.models.runtime_state import RuntimeState
from open_webui.models.tags import Tag
from open_webui.models.tools import Tool
from open_webui.models.users import User
from open_webui.services.deletion import platform_offboarding as local
from open_webui.services.deletion import platform_offboarding_vectors as vectors
from open_webui.soev import offboarding
from open_webui.test.soev.fake_api import person_ref
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import scoped_session, sessionmaker

SOURCE, TARGET, OTHER = 'fictional-cobalt', 'fictional-quartz', 'fictional-jasper'
CONTENT = (Model, Prompt, Tool, Function, Note, File, Memory, Calendar)


class ExternalState:
    def __init__(self):
        self.effects = Counter()
        self.blobs = {'fictional-source-object', 'fictional-other-object', 'fictional-cloud-object'}
        self.memory = {SOURCE: True, OTHER: True}
        self.files = {'file-' + SOURCE, 'file-' + OTHER, 'fictional-cloud-file'}

    async def memories(self, source, target):
        if source in self.memory:
            self.memory.pop(source)
            if target:
                self.memory[target] = True
            self.effects['memory'] += 1

    async def file_vectors(self, id):
        if id in self.files:
            self.files.remove(id)
            self.effects['file'] += 1

    async def storage(self, paths):
        for path in paths:
            if path in self.blobs:
                self.blobs.remove(path)
                self.effects['blob'] += 1


def task(mode='delete', **changes):
    return offboarding.Task.model_validate(
        {
            'id': 'fictional-job',
            'person': person_ref('cobalt'),
            'mode': mode,
            'target': person_ref('quartz') if mode == 'transfer' else None,
            'expires_at': '2027-11-10T00:00:00Z' if mode == 'archive' else None,
            **changes,
        }
    )


@pytest_asyncio.fixture
async def product(monkeypatch):
    dsn = os.environ.get('CONNECT_TEST_DSN')
    if not dsn:
        pytest.skip('CONNECT_TEST_DSN required')
    url = dsn.replace('postgresql://', 'postgresql+psycopg://', 1)
    schema = 'fictional_offboarding_' + uuid4().hex
    admin = create_async_engine(url)
    async with admin.begin() as conn:
        await conn.execute(text('CREATE SCHEMA ' + schema))
    engine = create_async_engine(url, connect_args={'options': '-c search_path=' + schema})
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    @asynccontextmanager
    async def database():
        async with sessions() as db:
            yield db

    monkeypatch.setattr(local, 'get_async_db', database)
    external = ExternalState()
    monkeypatch.setattr(vectors, 'memory_vectors', external.memories)
    monkeypatch.setattr(vectors, 'remove_file_vectors', external.file_vectors)
    monkeypatch.setattr(local, 'delete_storage_files', external.storage)
    async with sessions() as db:
        for user, name in ((SOURCE, 'cobalt'), (TARGET, 'quartz'), (OTHER, 'jasper')):
            db.add(
                User(
                    id=user,
                    name='Fictional ' + name,
                    email=name + '@example.test',
                    role='user',
                    person_id=person_ref(name),
                )
            )
        db.add(Auth(id=SOURCE, email='cobalt@example.test', password='fictional-hash', active=True))
        for owner in (SOURCE, OTHER):
            db.add_all(
                [
                    Model(id='model-' + owner, user_id=owner, name='Fictional assistant', params={}, meta={}),
                    Prompt(
                        id='prompt-' + owner,
                        user_id=owner,
                        command='fictional-' + owner,
                        name='Fictional prompt',
                        content='Fictional text',
                    ),
                    Tool(id='tool-' + owner, user_id=owner, name='Fictional tool', updated_at=1),
                    Function(id='function-' + owner, user_id=owner, name='Fictional function', type='filter'),
                    Note(id='note-' + owner, user_id=owner, title='Fictional note', data={'content': 'Fictional text'}),
                    File(
                        id='file-' + owner,
                        user_id=owner,
                        filename='fictional-input.txt',
                        path='fictional-source-object' if owner == SOURCE else 'fictional-other-object',
                        meta={},
                    ),
                    Memory(id='memory-' + owner, user_id=owner, content='Fictional memory'),
                    Calendar(
                        id='calendar-' + owner, user_id=owner, name='Fictional calendar', created_at=1, updated_at=1
                    ),
                    CalendarEvent(
                        id='event-' + owner,
                        user_id=owner,
                        calendar_id='calendar-' + owner,
                        title='Fictional event',
                        start_at=1,
                        created_at=1,
                        updated_at=1,
                    ),
                    Folder(
                        id='folder-' + owner,
                        user_id=owner,
                        name='Fictional folder',
                        items=[{'type': 'note', 'id': 'note-' + owner}],
                        data={},
                    ),
                    Chat(
                        id='chat-' + owner,
                        user_id=owner,
                        thread_id='thread-' + owner,
                        title='Fictional title',
                        chat={},
                        folder_id='folder-' + owner,
                        created_at=1,
                        updated_at=1,
                    ),
                    Tag(id='fictional-tag', user_id=owner, name='Fictional tag'),
                    AccessGrant(
                        id='grant-' + owner,
                        resource_type='note',
                        resource_id='note-' + owner,
                        principal_type='user',
                        principal_id=TARGET,
                        permission='read',
                        created_at=1,
                    ),
                    CalendarEventAttendee(
                        id='attendee-' + owner,
                        event_id='event-' + owner,
                        user_id=TARGET,
                        status='accepted',
                        created_at=1,
                        updated_at=1,
                    ),
                ]
            )
        db.add(Knowledge(id='knowledge-' + OTHER, user_id=OTHER, type='local', name='Fictional shared library'))
        db.add(
            File(
                id='fictional-cloud-file',
                user_id=SOURCE,
                filename='fictional-cloud.txt',
                path='fictional-cloud-object',
                meta={'source': 'fictional-drive'},
            )
        )
        db.add(
            AccessGrant(
                id='fictional-other-share',
                resource_type='note',
                resource_id='note-' + OTHER,
                principal_type='user',
                principal_id=SOURCE,
                permission='read',
                created_at=1,
            )
        )
        await db.commit()
    try:
        yield SimpleNamespace(
            sessions=sessions,
            url=url,
            schema=schema,
            effects=external.effects,
            blobs=external.blobs,
            memory=external.memory,
            files=external.files,
        )
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text('DROP SCHEMA ' + schema + ' CASCADE'))
        await admin.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['delete', 'transfer', 'archive'])
async def test_each_mode_commits_only_owned_local_rows_and_replays_receipt(product, monkeypatch, mode):
    from open_webui.services.deletion import offboarding as legacy

    forbidden = AsyncMock(side_effect=AssertionError('Legacy chat disposition must not run'))
    monkeypatch.setattr(legacy, 'offboard_user', forbidden)
    work = task(mode)
    assert await offboarding.apply(work) is None
    effects = product.effects.copy()
    assert await offboarding.apply(work) is None
    assert product.effects == effects
    forbidden.assert_not_awaited()
    async with product.sessions() as db:
        assert await db.get(User, SOURCE) is None
        assert await db.get(Auth, SOURCE) is None
        for table in CONTENT:
            own = await db.get(table, table.__tablename__ + '-' + SOURCE)
            other = await db.get(table, table.__tablename__ + '-' + OTHER)
            assert other is not None and other.user_id == OTHER
            if mode == 'transfer':
                assert own is not None and own.user_id == TARGET
            else:
                assert own is None
        assert await db.get(AccessGrant, 'grant-' + OTHER) is not None
        assert await db.get(AccessGrant, 'fictional-other-share') is not None
        assert await db.get(File, 'fictional-cloud-file') is None
        assert await db.get(CalendarEvent, 'event-' + OTHER) is not None
        assert (await db.get(Knowledge, 'knowledge-' + OTHER)).user_id == OTHER
        own_chat = await db.get(Chat, 'chat-' + SOURCE)
        own_folder = await db.get(Folder, 'folder-' + SOURCE)
        if mode == 'transfer':
            assert own_chat.user_id == own_folder.user_id == TARGET
            assert own_chat.thread_id == 'thread-' + SOURCE
            assert 'fictional-source-object' in product.blobs
            assert TARGET in product.memory and SOURCE not in product.memory
        elif mode == 'archive':
            assert own_chat.user_id == own_folder.user_id == 'platform:svc:archive-offboarding-' + work.id
            assert own_chat.thread_id == 'thread-' + SOURCE and own_chat.chat == {}
            assert own_folder.items == []
        else:
            assert own_chat is None and own_folder is None
        assert (await db.get(Chat, 'chat-' + OTHER)).user_id == OTHER
        assert (await db.get(Folder, 'folder-' + OTHER)).user_id == OTHER
        assert (await db.get(RuntimeState, local._key(work.person))).value['state'] == 'completed'
    assert 'fictional-other-object' in product.blobs
    assert OTHER in product.memory and 'file-' + OTHER in product.files
    if mode != 'transfer':
        assert 'fictional-source-object' not in product.blobs
        assert SOURCE not in product.memory and 'file-' + SOURCE not in product.files


@pytest.mark.asyncio
async def test_missing_target_and_source_refuse_without_mutation(product):
    assert await offboarding.apply(task('transfer', target=person_ref('absent'))) == 'target_missing'
    assert await offboarding.apply(task(person=person_ref('absent'))) == 'source_conflict'
    assert not product.effects
    async with product.sessions() as db:
        assert (await db.get(User, SOURCE)).role == 'user'
        assert await db.get(RuntimeState, local._key(person_ref('cobalt'))) is None


@pytest.mark.asyncio
async def test_cleanup_failure_keeps_rows_and_retry_does_not_repeat_committed_external_effects(product, monkeypatch):
    original = local._local
    failure = True

    async def fail_once(db, source, target, work):
        nonlocal failure
        await original(db, source, target, work)
        if failure:
            failure = False
            raise RuntimeError('fictional SQL failure')

    monkeypatch.setattr(local, '_local', fail_once)
    assert await offboarding.apply(task()) == 'local_cleanup_failed'
    first = product.effects.copy()
    async with product.sessions() as db:
        assert (await db.get(User, SOURCE)).role == 'pending'
        assert await db.get(Note, 'note-' + SOURCE) is not None
        assert (await db.get(RuntimeState, local._key(person_ref('cobalt')))).value['state'] == 'prepared'
    assert await offboarding.apply(task()) is None
    assert product.effects == first


@pytest.mark.asyncio
async def test_stale_job_or_changed_disposition_cannot_apply_again(product):
    assert await offboarding.apply(task()) is None
    first = product.effects.copy()
    assert await offboarding.apply(task('transfer')) == 'source_conflict'
    assert await offboarding.apply(task(person=person_ref('jasper'))) == 'source_conflict'
    assert product.effects == first
    async with product.sessions() as db:
        assert await db.get(Note, 'note-' + OTHER) is not None


@pytest.mark.asyncio
async def test_legacy_knowledge_requires_platform_ownership_provenance_before_mutation(product):
    async with product.sessions() as db:
        db.add(Knowledge(id='fictional-legacy-shadow', user_id=SOURCE, type='local', name='Fictional shared library'))
        await db.commit()
    assert await offboarding.apply(task()) == 'unsupported_content'
    assert not product.effects
    async with product.sessions() as db:
        assert (await db.get(User, SOURCE)).role == 'user'
        assert await db.get(Knowledge, 'fictional-legacy-shadow') is not None


@pytest.mark.asyncio
async def test_expired_archive_refuses_before_mutation_but_completed_receipt_replays(product, monkeypatch):
    work = task('archive')
    monkeypatch.setattr(local, '_expired', lambda _: True)
    assert await offboarding.apply(work) == 'unsupported_content'
    assert not product.effects
    async with product.sessions() as db:
        assert (await db.get(User, SOURCE)).role == 'user'
    monkeypatch.setattr(local, '_expired', lambda _: False)
    assert await offboarding.apply(work) is None
    first = product.effects.copy()
    monkeypatch.setattr(local, '_expired', lambda _: True)
    assert await offboarding.apply(work) is None
    assert product.effects == first


@pytest.mark.asyncio
async def test_pull_replays_success_after_lost_ack_without_repeating_local_effects(product, monkeypatch):
    remote = SimpleNamespace(get=AsyncMock(return_value=[task('transfer').model_dump(mode='json')]), send=AsyncMock())
    monkeypatch.setattr(offboarding, 'build_service_client', lambda: remote)
    monkeypatch.setattr(offboarding, 'v2_enabled', lambda: True)
    remote.send.side_effect = RuntimeError('fictional lost acknowledgement')
    with pytest.raises(RuntimeError, match='fictional lost acknowledgement'):
        await offboarding.poll()
    first = product.effects.copy()
    remote.send.side_effect = None
    await offboarding.poll()
    assert remote.send.await_args.args[2] == {'error': None}
    assert product.effects == first


@pytest.mark.asyncio
async def test_postgres_vector_disposition_preserves_other_vectors_and_existing_target(product):
    from open_webui.retrieval.vector.dbs.pgvector import PgvectorClient

    async with product.sessions() as db:
        # Opaque vector values exercise ownership mutation without an embedding service.
        await db.execute(text('CREATE TABLE document_chunk(id text PRIMARY KEY, vector text, collection_name text)'))
        await db.execute(
            text('INSERT INTO document_chunk VALUES (:id,:vector,:collection)'),
            [
                {'id': 'fictional-source', 'vector': '[1,0]', 'collection': 'user-memory-' + SOURCE},
                {'id': 'fictional-target', 'vector': '[0,1]', 'collection': 'user-memory-' + TARGET},
                {'id': 'fictional-other', 'vector': '[1,1]', 'collection': 'user-memory-' + OTHER},
                {'id': 'fictional-file', 'vector': '[0,0]', 'collection': 'file-' + SOURCE},
            ],
        )
        await db.commit()
    engine = create_engine(product.url, connect_args={'options': '-c search_path=' + product.schema})
    client = PgvectorClient.__new__(PgvectorClient)
    client.session = scoped_session(sessionmaker(engine))
    try:
        for _ in range(2):
            await asyncio.to_thread(vectors._move, client, 'user-memory-' + SOURCE, 'user-memory-' + TARGET)
            await asyncio.to_thread(vectors._move, client, 'file-' + SOURCE, None)
        async with product.sessions() as db:
            rows = (await db.execute(text('SELECT id,vector,collection_name FROM document_chunk ORDER BY id'))).all()
            assert rows == [
                ('fictional-other', '[1,1]', 'user-memory-' + OTHER),
                ('fictional-source', '[1,0]', 'user-memory-' + TARGET),
                ('fictional-target', '[0,1]', 'user-memory-' + TARGET),
            ]
    finally:
        await asyncio.to_thread(engine.dispose)
