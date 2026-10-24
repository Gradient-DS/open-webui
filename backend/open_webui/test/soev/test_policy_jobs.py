"""v2 refuses sessionless work before reads, remote calls or local changes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from open_webui import config, env
from open_webui.soev.client import SoevApiError


@pytest.fixture
def v2(monkeypatch):
    monkeypatch.setattr(env, 'AGENT_API_ENABLED', True)
    monkeypatch.setattr(config, 'SOEV_API_URL', 'https://platform.example.invalid')


@pytest.mark.asyncio
@pytest.mark.parametrize('boundary', ['inventory', 'offboard_user'])
async def test_offboarding_refuses_before_any_io(v2, monkeypatch, boundary):
    from open_webui.services.deletion import offboarding

    remote = Mock()
    read = AsyncMock()
    local = AsyncMock()
    monkeypatch.setattr(offboarding.identity, 'build_client', remote)
    monkeypatch.setattr(offboarding.Users, 'get_user_by_id', read)
    monkeypatch.setattr(offboarding, 'finalize_local', local)
    db = Mock()
    kwargs = {'db': db}
    if boundary == 'offboard_user':
        kwargs.update(actor_id='admin-fern', options=offboarding.OffboardingOptions())
    with pytest.raises(SoevApiError, match='Console offboarding required') as caught:
        await getattr(offboarding, boundary)('person-elm', **kwargs)
    assert caught.value.constraint == 'offboarding:console'
    remote.assert_not_called()
    read.assert_not_called()
    local.assert_not_called()
    assert db.mock_calls == []


@pytest.mark.asyncio
async def test_background_execution_stays_inert(v2, monkeypatch):
    from open_webui.utils import automations, subagents, timers

    read = AsyncMock()
    writes = Mock()
    remote = Mock()
    monkeypatch.setattr(automations.Users, 'get_user_by_id', read)
    monkeypatch.setattr(timers.Chats, 'get_chat_by_id', read)
    monkeypatch.setattr(timers, 'get_async_db', writes)
    monkeypatch.setattr(subagents, 'get_async_db', writes)
    monkeypatch.setattr(subagents, 'has_active_tasks', read)
    monkeypatch.setattr('open_webui.soev.identity.build_client', remote)
    await automations.scheduler_worker_loop(None)
    await automations.execute_automation(None, None)
    await automations._execute_channel_automation(None, None, None, '', '', '')
    await timers.execute_due_timer(None, 'timer-fern')
    assert await timers.claim_due_timers(0) == []
    await timers.cancel_timers_for_chat('chat-fern', 'chat.read', 'person-elm')
    await subagents.process_pending_internal_messages(None, 'chat-fern', 'person-elm', {})
    read.assert_not_called()
    writes.assert_not_called()
    remote.assert_not_called()


@pytest.mark.asyncio
async def test_timer_and_background_delegation_refuse_before_writes(v2, monkeypatch):
    from open_webui.utils import subagents, timers

    writes = Mock()
    monkeypatch.setattr(timers, 'get_async_db', writes)
    monkeypatch.setattr(subagents, 'get_async_db', writes)
    with pytest.raises(SoevApiError) as caught:
        await timers.create_timer(
            prompt='fictional reminder',
            at='',
            cancel_on=None,
            request=None,
            user_data={},
            metadata={},
            parent_chat_id='chat-fern',
            parent_message_id=None,
        )
    assert caught.value.constraint == 'automations:unavailable'
    with pytest.raises(SoevApiError):
        await subagents.delegate(
            '',
            '',
            True,
            request=None,
            user_data={},
            metadata={},
            parent_chat_id='chat-fern',
            parent_message_id=None,
        )
    writes.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'route', ['create_new_automation', 'update_automation_by_id', 'toggle_automation_by_id', 'run_automation_by_id']
)
async def test_automation_routes_refuse_before_reads_or_writes(v2, monkeypatch, route):
    from open_webui.routers import automations

    permission = AsyncMock()
    remote = Mock()
    monkeypatch.setattr(automations, 'check_automations_permission', permission)
    monkeypatch.setattr('open_webui.soev.identity.build_client', remote)
    kwargs = {'request': None, 'user': SimpleNamespace(id='person-elm'), 'db': Mock()}
    if route in {'create_new_automation', 'update_automation_by_id'}:
        kwargs['form_data'] = None
    if route != 'create_new_automation':
        kwargs['id'] = 'automation-fern'
    with pytest.raises(SoevApiError):
        await getattr(automations, route)(**kwargs)
    permission.assert_not_called()
    remote.assert_not_called()
    assert kwargs['db'].mock_calls == []


@pytest.mark.parametrize('enabled,url', [(False, 'https://platform.example.invalid'), (True, '')])
def test_legacy_mode_keeps_the_existing_boundaries(monkeypatch, enabled, url):
    from open_webui.soev.policy_jobs import refuse_automations, refuse_offboarding

    monkeypatch.setattr(env, 'AGENT_API_ENABLED', enabled)
    monkeypatch.setattr(config, 'SOEV_API_URL', url)
    refuse_automations()
    refuse_offboarding()


@pytest.mark.asyncio
@pytest.mark.parametrize('caller', ['model', 'service', 'scim', 'admin', 'preview'])
async def test_all_offboarding_callers_refuse_without_remote_calls_or_local_writes(v2, monkeypatch, caller):
    from fastapi import Request
    from open_webui.models.users import Users
    from open_webui.routers import scim, users
    from open_webui.services.deletion.service import DeletionService

    remote = Mock()
    monkeypatch.setattr('open_webui.soev.identity.build_client', remote)
    read = AsyncMock(return_value=SimpleNamespace(id='person-elm', role='user', email='elm@example.invalid'))
    monkeypatch.setattr(Users, 'get_user_by_id', read)
    monkeypatch.setattr(Users, 'get_scim_user_by_id', read)
    monkeypatch.setattr(Users, 'get_first_user', AsyncMock(return_value=SimpleNamespace(id='primary-fern')))
    db = Mock()
    request = Request({'type': 'http', 'headers': []})
    with pytest.raises(SoevApiError) as caught:
        if caller == 'model':
            await Users.delete_user_by_id('person-elm', db=db)
        elif caller == 'service':
            await DeletionService.delete_user('person-elm')
        elif caller == 'scim':
            await scim.delete_user('person-elm', request, True, db)
        elif caller == 'admin':
            await users.delete_user_by_id(
                request,
                'person-elm',
                mode='delete',
                transfer_to=None,
                confirm_shared_deletion=[],
                archive_retention_days=None,
                never_delete=False,
                archive_before_delete=False,
                archive_reason=None,
                user=SimpleNamespace(id='admin-fern'),
                db=db,
            )
        else:
            await users.get_user_shared_data('person-elm', user=SimpleNamespace(id='admin-fern'), db=db)
    assert caught.value.constraint == 'offboarding:console'
    assert db.mock_calls == []
    remote.assert_not_called()


@pytest.mark.asyncio
async def test_policy_editor_uses_admin_token_and_the_exact_platform_wire(v2, monkeypatch):
    from fastapi import Request
    from open_webui.routers import configs
    from open_webui.soev import acting

    remote = SimpleNamespace(
        get_retention_policy=AsyncMock(return_value=None), send=AsyncMock(return_value={'version': 1})
    )
    writes = AsyncMock()
    monkeypatch.setattr('open_webui.soev.identity.build_client', lambda: remote)
    monkeypatch.setattr(configs.Config, 'upsert', writes)
    monkeypatch.setattr(acting, 'requester_ref', lambda: 'owui:user:admin-fern')
    monkeypatch.setattr(acting, 'user_token', AsyncMock(return_value='fictional-user-token'))
    user = SimpleNamespace(id='admin-fern')
    request = Request({'type': 'http', 'headers': [(b'idempotency-key', b'fictional-policy-key')]})
    assert await configs.get_data_retention_config(request, user=user) == {'platform_policy': None}
    form = configs.PlatformRetentionConfigForm(temporary_idle_hours=24, thread_idle_days=None)
    result = await configs.set_data_retention_config(request, form, user=user)
    assert result == {'platform_policy': {'version': 1}}
    remote.get_retention_policy.assert_awaited_once_with(as_user='owui:user:admin-fern')
    remote.send.assert_awaited_once_with(
        'PUT',
        '/v1/retention-policy',
        {'temporary_idle_hours': 24, 'thread_idle_days': None},
        as_user='owui:user:admin-fern',
        idempotency_key='fictional-policy-key',
    )
    writes.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('method', ['read', 'write'])
async def test_policy_editor_refuses_a_missing_admin_token_before_io(v2, monkeypatch, method):
    from fastapi import Request
    from open_webui.routers import configs
    from open_webui.soev import acting

    remote, writes = Mock(), AsyncMock()
    monkeypatch.setattr('open_webui.soev.identity.build_client', remote)
    monkeypatch.setattr(configs.Config, 'upsert', writes)
    monkeypatch.setattr(acting, 'requester_ref', lambda: 'owui:user:admin-fern')
    monkeypatch.setattr(acting, 'user_token', AsyncMock(return_value=None))
    request = Request({'type': 'http', 'headers': []})
    with pytest.raises(SoevApiError):
        if method == 'read':
            await configs.get_data_retention_config(request, user=SimpleNamespace(id='admin-fern'))
        else:
            await configs.set_data_retention_config(
                request,
                configs.PlatformRetentionConfigForm(temporary_idle_hours=None, thread_idle_days=None),
                user=SimpleNamespace(id='admin-fern'),
            )
    remote.assert_not_called()
    writes.assert_not_called()


@pytest.mark.asyncio
async def test_timer_and_automation_tools_are_not_exposed_under_v2(v2, monkeypatch):
    from fastapi import Request
    from open_webui.utils import tools

    monkeypatch.setattr(
        tools.Config,
        'get_many',
        AsyncMock(
            return_value={
                'subagents.enable': True,
                'subagents.background_enabled': True,
                'automations.enable': True,
            }
        ),
    )
    result = await tools.get_builtin_tools(
        Request({'type': 'http', 'headers': []}),
        {'__user__': {'id': 'person-elm', 'role': 'admin'}},
    )
    assert 'timer' not in result
    assert 'create_automation' not in result
    assert 'update_automation' not in result
    assert 'background' not in result['delegate_task']['spec']['parameters']['properties']


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['get', 'delete', 'export'])
@pytest.mark.parametrize('service', [False, True])
async def test_thread_transport_refuses_sessionless_reads_and_deletes(v2, monkeypatch, operation, service):
    from open_webui.soev import acting, client

    network = Mock()
    monkeypatch.setattr(client, '_shared_client', network)
    monkeypatch.setattr(acting, 'requester_ref', lambda: 'owui:user:person-elm')
    monkeypatch.setattr(acting, 'user_token', AsyncMock(return_value=None))
    caller = client.SoevClient(
        'https://platform.example.invalid', 'fictional-key', subject_minter=lambda _: '', service=service
    )
    with pytest.raises(SoevApiError):
        if operation == 'get':
            await caller.get('/v1/chat/threads/thread-elm', as_user='owui:user:person-elm')
        elif operation == 'delete':
            await caller.chat_delete('/v1/chat/threads/thread-elm', as_user='owui:user:person-elm')
        else:
            async for _ in caller.stream('/v1/chat/threads/thread-elm/export', as_user='owui:user:person-elm'):
                pass
    network.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [200, 202, 204])
async def test_v2_thread_delete_requires_a_completed_204(v2, monkeypatch, status):
    import httpx
    from open_webui.soev.client import SoevClient

    caller = SoevClient('https://platform.example.invalid', 'fictional-key')
    monkeypatch.setattr(caller, '_request', AsyncMock(return_value=httpx.Response(status)))
    if status == 204:
        assert await caller.chat_delete('/v1/chat/threads/thread-elm', as_user='owui:user:person-elm') is None
    else:
        with pytest.raises(SoevApiError):
            await caller.chat_delete('/v1/chat/threads/thread-elm', as_user='owui:user:person-elm')


def test_retention_policy_routes_are_admin_only(monkeypatch):
    """A non-admin never reaches the platform policy: both routes depend on the admin check."""
    from fastapi import HTTPException

    from open_webui.routers import configs
    from open_webui.utils.auth import get_admin_user

    routes = {
        (method, route.path): route
        for route in configs.router.routes
        for method in getattr(route, 'methods', ())
        if route.path == '/data-retention'
    }
    assert set(routes) == {('GET', '/data-retention'), ('POST', '/data-retention')}
    for route in routes.values():
        assert get_admin_user in {dependency.call for dependency in route.dependant.dependencies}
    with pytest.raises(HTTPException) as refused:
        get_admin_user(SimpleNamespace(role='user'))
    assert refused.value.status_code == 401
