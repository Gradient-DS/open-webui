"""Retired approvals refuse without consulting thread or platform state."""

from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from open_webui.soev import threads
from open_webui.utils.auth import get_verified_user


@pytest.fixture
def approval_route(monkeypatch):
    main = import_module('open_webui.main')
    path = '/api/v1/chats/{id}/messages/{message_id}/resolve'
    route = next(route for route in main.app.routes if getattr(route, 'path', None) == path)
    router = APIRouter()
    router.routes.append(route)
    app = FastAPI()
    app.include_router(router)
    app.add_exception_handler(threads.ThreadError, main.thread_error_handler)
    platform = Mock(side_effect=AssertionError('Retired approvals must not build a platform client'))
    legacy = AsyncMock(side_effect=AssertionError('Retired approvals must not read local messages'))
    session = Mock(side_effect=AssertionError('Retired approvals must not open a database session'))
    monkeypatch.setattr(threads, 'enabled', lambda: True)
    monkeypatch.setattr(threads, 'build_client', platform)
    monkeypatch.setattr(main, 'resolve_tool_call_output', legacy)
    app.dependency_overrides[main.get_async_session] = lambda: session()
    return SimpleNamespace(app=app, main=main, platform=platform, legacy=legacy, session=session)


@pytest.mark.parametrize('actor', ['owner', 'stranger', 'admin', 'anonymous'])
@pytest.mark.parametrize('chat_id,message_id', [('fictional-chat', 'fictional-message'), ('missing', 'missing')])
@pytest.mark.parametrize(
    'body',
    [
        {'call_id': 'fictional-call', 'action': 'approve'},
        {'call_id': 'fictional-call', 'action': 'reject'},
        {'call_id': 'fictional-call', 'action': 'answer', 'answers': {'fictional-question': 'fictional-answer'}},
        {},
        None,
    ],
)
def test_platform_approval_is_retired_for_every_actor_before_thread_access(
    approval_route, actor, chat_id, message_id, body
):
    if actor != 'anonymous':
        user = SimpleNamespace(id=f'fictional-{actor}', role='admin' if actor == 'admin' else 'user')
        approval_route.app.dependency_overrides[get_verified_user] = lambda: user
    with TestClient(approval_route.app) as client:
        response = client.post(f'/api/v1/chats/{chat_id}/messages/{message_id}/resolve', json=body)
    assert response.status_code == (401 if actor == 'anonymous' else 409), response.text
    assert response.json() == ({'detail': 'Not authenticated'} if actor == 'anonymous' else {'state': 'unavailable'})
    approval_route.platform.assert_not_called()
    approval_route.legacy.assert_not_awaited()
    approval_route.session.assert_not_called()


def test_legacy_approval_resolution_remains_available(approval_route, monkeypatch):
    main = approval_route.main
    user = SimpleNamespace(id='fictional-owner', role='user')
    approval_route.app.dependency_overrides[get_verified_user] = lambda: user
    approval_route.session.side_effect = None
    approval_route.session.return_value = 'fictional-session'
    approval_route.legacy.side_effect = None
    approval_route.legacy.return_value = {'chat': {'id': 'fictional-chat'}}
    payload = {'fictional': 'resume'}
    resume = AsyncMock(return_value=payload)
    complete = AsyncMock(return_value={'task_ids': ['fictional-task']})
    monkeypatch.setattr(threads, 'enabled', lambda: False)
    monkeypatch.setattr(main, 'build_tool_approval_resume_payload', resume)
    monkeypatch.setattr(main, 'chat_completion', complete)
    with TestClient(approval_route.app) as client:
        response = client.post(
            '/api/v1/chats/fictional-chat/messages/fictional-message/resolve',
            json={'call_id': 'fictional-call', 'action': 'reject'},
        )
    assert response.status_code == 200, response.text
    assert response.json() == {
        'status': True,
        'chat_id': 'fictional-chat',
        'message_id': 'fictional-message',
        'task_ids': ['fictional-task'],
    }
    approval_route.legacy.assert_awaited_once()
    args, kwargs = approval_route.legacy.await_args
    assert args[:2] == ('fictional-chat', 'fictional-message')
    assert args[2].model_dump() == {
        'call_id': 'fictional-call',
        'action': 'reject',
        'answers': None,
        'timed_out': False,
    }
    assert args[3] is user
    assert kwargs == {'db': 'fictional-session'}
    resume.assert_awaited_once_with('fictional-chat', 'fictional-message', chat={'id': 'fictional-chat'})
    assert complete.await_args.args[1:] == (payload, user)
    approval_route.platform.assert_not_called()
