"""Local deletion mirrors retain the feed cursor and never touch remote content."""

import os
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio
from open_webui import config, env
from open_webui.models.chat_messages import ChatMessage
from open_webui.models.chats import Chat, ChatFile, Chats
from open_webui.models.config import Config
from open_webui.models.files import File
from open_webui.soev import acting, agent_threads, retention
from open_webui.soev.client import SoevApiError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.fixture(params=['sqlite', 'postgres'])
def mirror_url(request, tmp_path):
    if request.param == 'postgres':
        if not os.environ.get('BACKEND_TEST_DATABASE_URL'):
            pytest.skip('BACKEND_TEST_DATABASE_URL is not set')
        return request.getfixturevalue('postgres_database')
    return f'sqlite+aiosqlite:///{tmp_path}/mirror.db'


@pytest_asyncio.fixture
async def mirror(mirror_url, monkeypatch):
    from open_webui.internal import db as database
    from open_webui.models.access_grants import AccessGrant
    from open_webui.models.automations import AutomationRun
    from open_webui.models.folders import Folder
    from open_webui.models.shared_chats import SharedChat

    monkeypatch.setattr(env, 'AGENT_API_ENABLED', True)
    monkeypatch.setattr(config, 'SOEV_API_URL', 'https://platform.example.invalid')
    monkeypatch.setattr(config, 'SOEV_API_KEY', 'soev_test_cred-fictional_test-secret')
    monkeypatch.setattr(database, 'DATABASE_ENABLE_SESSION_SHARING', True)
    engine = create_async_engine(mirror_url)
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda sync: database.Base.metadata.create_all(
                sync,
                tables=[
                    model.__table__
                    for model in (
                        Chat,
                        File,
                        ChatFile,
                        ChatMessage,
                        Config,
                        SharedChat,
                        Folder,
                        AutomationRun,
                        AccessGrant,
                    )
                ],
            )
        )
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def context():
        async with sessions() as db:
            yield db

    monkeypatch.setattr(retention, 'get_async_db', context)
    monkeypatch.setattr(database, 'get_async_db', context)
    remote = SimpleNamespace(get=AsyncMock(), chat_delete=AsyncMock())
    monkeypatch.setattr(retention, 'build_service_client', lambda: remote)
    monkeypatch.setattr(agent_threads.identity, 'build_client', lambda: remote)
    monkeypatch.setattr(acting, 'requester_ref', lambda: 'owui:user:fern')
    monkeypatch.setattr(acting, 'user_token', AsyncMock(return_value='fictional-user-token'))
    yield SimpleNamespace(sessions=sessions, remote=remote)
    await engine.dispose()


async def seed(mirror, chat_id, threads, *, owner='fern', pending=False, folder=None, parent=None):
    messages = {
        f'{chat_id}-a{i}': {
            'id': f'{chat_id}-a{i}',
            'role': 'assistant',
            'content': '',
            'meta': {'agent_v2': {'thread_id': thread}},
        }
        for i, thread in enumerate(threads)
    }
    async with mirror.sessions() as db:
        db.add(
            Chat(
                id=chat_id,
                user_id=owner,
                title='Fictional chat',
                chat={'history': {'messages': messages}},
                meta={'internal': True, 'parent_chat_id': parent} if parent else {},
                created_at=1,
                updated_at=1,
                deleted_at=2 if pending else None,
                folder_id=folder,
            )
        )
        await db.flush()
        for i, thread in enumerate(threads):
            db.add(
                ChatMessage(
                    id=f'{chat_id}-a{i}',
                    chat_id=chat_id,
                    user_id=owner,
                    role='assistant',
                    content='',
                    meta={'agent_v2': {'thread_id': thread}},
                    created_at=1,
                    updated_at=1,
                )
            )
        await db.commit()


async def chat_ids(mirror, *, live=False):
    async with mirror.sessions() as db:
        query = select(Chat.id).where(Chat.deleted_at.is_(None)) if live else select(Chat.id)
        return set((await db.execute(query)).scalars())


def page(*threads, next_cursor=None):
    return {
        'data': [
            {'thread_id': thread, 'policy_version': 1, 'deleted_at': '2026-10-24T12:00:00Z'} for thread in threads
        ],
        'next_cursor': next_cursor,
    }


@pytest.mark.asyncio
async def test_feed_pages_keep_cursor_and_only_remove_local_rows(mirror):
    await seed(mirror, 'chat-fern', ['thread-elm', 'thread-oak'])
    await seed(mirror, 'clone-fern', ['thread-elm'])
    await seed(mirror, 'chat-other', ['thread-pine'])
    mirror.remote.get.side_effect = [page('thread-elm', next_cursor='thread-elm'), page('thread-oak'), page()]
    await retention.poll_deletions()
    assert await chat_ids(mirror, live=True) == {'chat-fern', 'chat-other'}
    await retention.poll_deletions()
    assert await chat_ids(mirror, live=True) == {'chat-other'}
    await retention.poll_deletions()
    assert mirror.remote.get.call_args_list[-1].kwargs == {'params': {'limit': 100, 'after': 'thread-oak'}}
    mirror.remote.chat_delete.assert_not_called()
    assert len(mirror.remote.get.call_args_list) == 3
    assert all(call.args == ('/v1/retention/deletions',) for call in mirror.remote.get.call_args_list)


@pytest.mark.asyncio
async def test_failed_local_cleanup_does_not_advance_cursor(mirror, monkeypatch):
    await seed(mirror, 'chat-fern', ['thread-elm'])
    mirror.remote.get.return_value = page('thread-elm')
    monkeypatch.setattr(Chats, 'soft_delete_by_id', AsyncMock(side_effect=RuntimeError('fictional failure')))
    with pytest.raises(RuntimeError, match='fictional failure'):
        await retention.poll_deletions()
    assert await chat_ids(mirror, live=True) == {'chat-fern'}
    async with mirror.sessions() as db:
        assert await db.get(Config, retention._key()) is None
    mirror.remote.chat_delete.assert_not_called()


@pytest.mark.asyncio
async def test_pending_worker_cleans_locally_without_acting_for_anyone(mirror):
    from open_webui.services.deletion.cleanup_worker import _process_pending_chat_deletions

    await seed(mirror, 'confirmed', ['thread-elm'], pending=True)
    await seed(mirror, 'waiting', ['thread-oak'], pending=True)
    await seed(mirror, 'local-only', [], pending=True)
    await retention.remember_deleted({'thread-elm'})
    await _process_pending_chat_deletions()
    assert await chat_ids(mirror) == set()
    mirror.remote.get.assert_not_called()
    mirror.remote.chat_delete.assert_not_called()


@pytest.mark.asyncio
async def test_batch_deletion_counts_every_surviving_bookmark(mirror):
    await seed(mirror, 'chat-fern', ['thread-elm', 'thread-oak'])
    await seed(mirror, 'child-fern', ['thread-elm'], parent='chat-fern')
    # A clone the same user owns shares the original's threads; another user's clone bookmarks none.
    await seed(mirror, 'clone-fern', ['thread-oak'])
    await agent_threads.delete_chats_threads('fern', ['chat-fern', 'child-fern'])
    mirror.remote.chat_delete.assert_awaited_once_with('/v1/chat/threads/thread-elm', as_user='owui:user:fern')
    assert await chat_ids(mirror) == {'chat-fern', 'child-fern', 'clone-fern'}


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [400, 403, 409, 429, 500, 503])
async def test_every_remote_failure_refuses_before_local_changes(mirror, status):
    await seed(mirror, 'chat-fern', ['thread-elm'])
    mirror.remote.chat_delete.side_effect = SoevApiError(status, 'refused', 'fictional failure')
    with pytest.raises(SoevApiError):
        await agent_threads.delete_chats_threads('fern', ['chat-fern'])
    assert await chat_ids(mirror) == {'chat-fern'}
    async with mirror.sessions() as db:
        assert await db.get(Config, retention._key()) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('requester,token', [('owui:user:birch', 'fictional-token'), ('owui:user:fern', None)])
async def test_missing_owner_request_or_token_refuses_without_remote_calls(mirror, monkeypatch, requester, token):
    await seed(mirror, 'chat-fern', ['thread-elm'])
    monkeypatch.setattr(acting, 'requester_ref', lambda: requester)
    monkeypatch.setattr(acting, 'user_token', AsyncMock(return_value=token))
    with pytest.raises(SoevApiError) as caught:
        await agent_threads.delete_chats_threads('fern', ['chat-fern'])
    assert caught.value.constraint == 'thread:owner_request'
    mirror.remote.chat_delete.assert_not_called()
    mirror.remote.get.assert_not_called()
    assert await chat_ids(mirror) == {'chat-fern'}


@pytest.mark.asyncio
async def test_admin_can_remove_a_confirmed_gone_bookmark_without_owner_token(mirror, monkeypatch):
    await seed(mirror, 'chat-fern', ['thread-elm'])
    await retention.remember_deleted({'thread-elm'})
    monkeypatch.setattr(acting, 'requester_ref', lambda: 'owui:user:admin-birch')
    monkeypatch.setattr(acting, 'user_token', AsyncMock(return_value=None))
    await agent_threads.delete_chats_threads('fern', ['chat-fern'])
    mirror.remote.chat_delete.assert_not_called()
    mirror.remote.get.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('route', ['chat', 'bulk', 'folder', 'message', 'service'])
@pytest.mark.parametrize('refusal', ['admin', 'token', 'upstream'])
async def test_deletion_entry_points_leave_all_local_data_on_refusal(mirror, monkeypatch, route, refusal):
    from fastapi import Request
    from open_webui.models.folders import Folder
    from open_webui.routers import chats, folders
    from open_webui.services.deletion.service import DeletionService

    await seed(mirror, 'chat-fern', ['thread-elm'], folder='folder-fern')
    await seed(mirror, 'child-fern', ['thread-oak'], parent='chat-fern')
    user = SimpleNamespace(id='fern', role='admin')
    if refusal == 'admin':
        if route in {'bulk', 'service'}:
            # These helpers operate on the supplied owner's batch; no separate target id is accepted.
            user.id = 'fern'
        else:
            user.id = 'admin-birch'
        monkeypatch.setattr(acting, 'requester_ref', lambda: 'owui:user:admin-birch')
    elif refusal == 'token':
        monkeypatch.setattr(acting, 'user_token', AsyncMock(return_value=None))
    else:
        mirror.remote.chat_delete.side_effect = SoevApiError(503, 'upstream_error', 'fictional failure')
    monkeypatch.setattr(config, 'ENABLE_ADMIN_CHAT_ACCESS', True)
    monkeypatch.setattr(config, 'BYPASS_ADMIN_ACCESS_CONTROL', True)
    monkeypatch.setattr(chats, 'has_permission', AsyncMock(return_value=True))
    monkeypatch.setattr(folders, 'has_permission', AsyncMock(return_value=True))
    monkeypatch.setattr(folders, 'check_folders_permission', AsyncMock())
    stop_tasks = AsyncMock()
    publish = AsyncMock()
    monkeypatch.setattr(chats, 'stop_item_tasks', stop_tasks)
    monkeypatch.setattr(chats, 'publish_event', publish)
    monkeypatch.setattr(folders, 'publish_event', publish)
    request = Request({'type': 'http', 'app': SimpleNamespace(state=SimpleNamespace(redis=None)), 'headers': []})
    async with mirror.sessions() as db:
        db.add(Folder(id='folder-fern', user_id='fern', name='Fictional folder', data={}, created_at=1, updated_at=1))
        await db.commit()
        with pytest.raises(SoevApiError):
            if route == 'chat':
                await chats.delete_chat_by_id(request, 'chat-fern', user=user, db=db)
            elif route == 'bulk':
                await chats.delete_all_user_chats(request, user=user, db=db)
            elif route == 'folder':
                await folders.delete_folder_by_id(request, 'folder-fern', True, user=user, db=db)
            elif route == 'message':
                await chats.delete_chat_message_by_id(request, 'chat-fern', 'chat-fern-a0', user=user, db=db)
            else:
                await DeletionService.delete_chat('chat-fern', 'fern')
        await db.rollback()
    assert await chat_ids(mirror) == {'chat-fern', 'child-fern'}
    async with mirror.sessions() as db:
        assert (await db.get(Chat, 'chat-fern')).deleted_at is None
        assert (await db.get(Chat, 'child-fern')).deleted_at is None
        assert await db.get(ChatMessage, 'chat-fern-a0') is not None
        assert await db.get(Folder, 'folder-fern') is not None
        assert await db.get(Config, retention._key()) is None
    stop_tasks.assert_not_called()
    publish.assert_not_called()
    mirror.remote.get.assert_not_called()
    if refusal != 'upstream':
        mirror.remote.chat_delete.assert_not_called()


@pytest.mark.asyncio
async def test_message_delete_preflight_keeps_a_thread_another_message_still_uses(mirror):
    await seed(mirror, 'chat-fern', ['thread-elm', 'thread-elm', 'thread-oak'])
    await agent_threads.delete_message_threads('fern', 'chat-fern', 'chat-fern-a0')
    mirror.remote.chat_delete.assert_not_called()
    await agent_threads.delete_message_threads('fern', 'chat-fern', 'chat-fern-a2')
    mirror.remote.chat_delete.assert_awaited_once_with('/v1/chat/threads/thread-oak', as_user='owui:user:fern')


@pytest.mark.asyncio
async def test_retention_under_v2_only_counts_console_offboarding_candidates(mirror, monkeypatch):
    from open_webui.services.retention import service
    from open_webui.services.retention.service import DataRetentionService

    inactive = AsyncMock(return_value=1)
    warnings = AsyncMock()
    delete = AsyncMock()
    monkeypatch.setattr(service, '_inactive_user_count', inactive)
    monkeypatch.setattr(DataRetentionService, '_send_warning_emails', warnings)
    monkeypatch.setattr(Chats, 'soft_delete_by_id', delete)
    report = await DataRetentionService.run_cleanup(None, 30, 30, 30, 0, enable_warning_email=True)
    assert report.users_deleted == report.chats_deleted == report.warnings_sent == 0
    inactive.assert_awaited_once()
    warnings.assert_not_called()
    delete.assert_not_called()
    mirror.remote.get.assert_not_called()
    mirror.remote.chat_delete.assert_not_called()


@pytest.mark.asyncio
async def test_chat_completion_leaves_preexisting_internal_messages_pending(mirror, monkeypatch):
    import importlib

    from fastapi import Request

    monkeypatch.setattr('open_webui.soev.identity.validate_config', lambda: None)
    main = importlib.import_module('open_webui.main')
    from open_webui.utils import subagents

    await seed(mirror, 'chat-fern', [])
    pending = {
        'id': 'pending-elm',
        'role': 'user',
        'content': 'Fictional pending work',
        'childrenIds': [],
        'meta': {'internal': True, 'type': 'subagent', 'status': 'pending'},
    }
    async with mirror.sessions() as db:
        row = await db.get(Chat, 'chat-fern')
        row.chat = {'history': {'messages': {'pending-elm': pending}}}
        db.add(
            ChatMessage(
                id='pending-elm',
                chat_id='chat-fern',
                user_id='fern',
                role='user',
                content=pending['content'],
                meta=pending['meta'],
                created_at=1,
                updated_at=1,
            )
        )
        await db.commit()
    monkeypatch.setattr(main, 'AGENT_API_ENABLED', True)
    monkeypatch.setattr(main, 'BYPASS_MODEL_ACCESS_CONTROL', True)
    monkeypatch.setattr(main.Models, 'get_model_by_id', AsyncMock(return_value=None))
    monkeypatch.setattr(main.Config, 'get', AsyncMock(side_effect=lambda key, default=None: default))
    monkeypatch.setattr(main.system_prompt, 'for_model', AsyncMock(return_value=None))
    for name in ('update_chat_by_id', 'update_chat_variables_by_id', 'upsert_message_to_chat_by_id_and_message_id'):
        monkeypatch.setattr(main.Chats, name, AsyncMock())

    async def payload(request, form, user, metadata, model):
        return form, metadata, []

    reply = {'choices': [{'message': {'content': 'Fictional answer'}}]}
    monkeypatch.setattr(main, 'process_chat_payload', payload)
    monkeypatch.setattr(main, 'call_agent_api', AsyncMock(return_value=reply))
    monkeypatch.setattr(main, 'build_chat_response_context', AsyncMock(return_value=None))
    monkeypatch.setattr(main, 'process_chat_response', AsyncMock(return_value=reply))
    monkeypatch.setattr(main, 'has_active_tasks', AsyncMock(return_value=False))
    pending_call = AsyncMock(wraps=subagents.process_pending_internal_messages)
    monkeypatch.setattr(subagents, 'process_pending_internal_messages', pending_call)
    pipeline = Mock()
    monkeypatch.setattr(subagents, '_build_request', pipeline)
    app = SimpleNamespace(state=SimpleNamespace(redis=None, MODELS={'fictional-model': {'id': 'fictional-model'}}))
    request = Request({'type': 'http', 'headers': [], 'app': app})
    result = await main.chat_completion(
        request,
        {
            'model': 'fictional-model',
            'chat_id': 'chat-fern',
            'messages': [{'role': 'user', 'content': 'Hello'}],
            'stream': False,
        },
        SimpleNamespace(id='fern', role='admin'),
    )
    assert result == reply
    pending_call.assert_awaited_once()
    pipeline.assert_not_called()
    async with mirror.sessions() as db:
        assert (await db.get(Chat, 'chat-fern')).chat['history']['messages']['pending-elm'] == pending
        assert (await db.get(ChatMessage, 'pending-elm')).meta['status'] == 'pending'
    mirror.remote.get.assert_not_called()
    mirror.remote.chat_delete.assert_not_called()


@pytest.mark.asyncio
async def test_owner_chat_deletion_removes_remote_threads_before_pending_local_cleanup(mirror, monkeypatch):
    from fastapi import Request
    from open_webui.routers import chats
    from open_webui.services.deletion.cleanup_worker import _process_pending_chat_deletions

    await seed(mirror, 'chat-fern', ['thread-elm'])
    await seed(mirror, 'child-fern', ['thread-oak'], parent='chat-fern')
    monkeypatch.setattr(config, 'ENABLE_ADMIN_CHAT_ACCESS', True)
    monkeypatch.setattr(chats, 'stop_item_tasks', AsyncMock())
    monkeypatch.setattr(chats, 'publish_event', AsyncMock())
    monkeypatch.setattr(Chats, 'delete_orphan_tags_for_user', AsyncMock())
    request = Request({'type': 'http', 'headers': [], 'app': SimpleNamespace(state=SimpleNamespace(redis=None))})
    async with mirror.sessions() as db:
        assert await chats.delete_chat_by_id(request, 'chat-fern', user=SimpleNamespace(id='fern', role='admin'), db=db)
    assert {call.args[0] for call in mirror.remote.chat_delete.call_args_list} == {
        '/v1/chat/threads/thread-elm',
        '/v1/chat/threads/thread-oak',
    }
    async with mirror.sessions() as db:
        assert (await db.get(Chat, 'chat-fern')).deleted_at is not None
        assert (await db.get(Chat, 'child-fern')).deleted_at is not None
    mirror.remote.chat_delete.reset_mock()
    await _process_pending_chat_deletions()
    assert await chat_ids(mirror) == set()
    mirror.remote.chat_delete.assert_not_called()
    mirror.remote.get.assert_not_called()


@pytest.mark.asyncio
async def test_cursor_survives_secret_rotation_and_feed_failure(mirror, monkeypatch):
    mirror.remote.get.return_value = page('thread-elm')
    await retention.poll_deletions()
    monkeypatch.setattr(config, 'SOEV_API_KEY', 'soev_test_cred-fictional_rotated-secret')
    mirror.remote.get.side_effect = SoevApiError(503, 'upstream_error', 'Fictional outage')
    with pytest.raises(SoevApiError):
        await retention.poll_deletions()
    assert mirror.remote.get.call_args.kwargs == {'params': {'limit': 100, 'after': 'thread-elm'}}
    async with mirror.sessions() as db:
        assert (await db.get(Config, retention._key())).value['cursor'] == 'thread-elm'


@pytest.mark.asyncio
async def test_admin_message_deletion_refuses_even_when_the_message_has_no_bookmark(mirror, monkeypatch):
    await seed(mirror, 'chat-fern', ['thread-elm'])
    async with mirror.sessions() as db:
        row = await db.get(Chat, 'chat-fern')
        row.chat = {
            'history': {
                'messages': {
                    **row.chat['history']['messages'],
                    'local-message': {'id': 'local-message', 'role': 'user', 'content': 'Hello'},
                }
            }
        }
        await db.commit()
    monkeypatch.setattr(acting, 'requester_ref', lambda: 'owui:user:admin-birch')
    with pytest.raises(SoevApiError):
        await agent_threads.delete_message_threads('fern', 'chat-fern', 'local-message')
    mirror.remote.get.assert_not_called()
    mirror.remote.chat_delete.assert_not_called()
    async with mirror.sessions() as db:
        assert 'local-message' in (await db.get(Chat, 'chat-fern')).chat['history']['messages']
