"""Platform groups have one writer; OWUI mirrors and configures product features."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from open_webui.models.groups import GroupMember, Groups, GroupUpdateForm
from open_webui.soev import group_mirror
from sqlalchemy import select


def row(id='old', members=None):
    return {
        'id': id,
        'ref': f'platform:group:{id}',
        'name': 'Meadow',
        'members': ['platform:user:lark-person', 'missing-person'] if members is None else members,
    }


@pytest.mark.asyncio
async def test_pull_pages_replaces_members_and_preserves_product_settings(group_db, monkeypatch):
    requests = []
    resolve_token = AsyncMock(side_effect=AssertionError('Directory mirroring uses service authority'))
    monkeypatch.setattr('open_webui.soev.client.user_token', resolve_token)

    def handle(request):
        requests.append(request)
        assert request.method == 'GET'
        assert request.url.path == '/v1/groups'
        assert request.headers['Authorization'] == 'Bearer soev_test_cred-runtime_test-secret'
        assert 'X-Soev-User-Token' not in request.headers
        assert 'X-Soev-' + 'Subject' not in request.headers
        if request.url.params.get('cursor') == 'page-2':
            return httpx.Response(200, json={'data': [row('new', ['fern-person'])], 'next_cursor': None})
        return httpx.Response(200, json={'data': [row()], 'next_cursor': 'page-2'})

    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        'open_webui.soev.client.httpx.AsyncClient',
        lambda **kw: client_type(transport=httpx.MockTransport(handle), **kw),
    )
    await group_mirror.reconcile(db=group_db)
    assert len(requests) == 2
    resolve_token.assert_not_awaited()
    old = await Groups.get_group_by_id('old', db=group_db)
    assert old.name == 'Meadow'
    assert old.permissions == {'workspace': {'knowledge': True}}
    assert old.data == {'config': {'share': 'members'}}
    assert await Groups.get_group_user_ids_by_id('old', db=group_db) == ['lark']
    assert await Groups.get_group_user_ids_by_id('new', db=group_db) == ['fern']
    assert [g.id for g in await Groups.get_groups_by_member_id('unlinked', db=group_db)] == []
    assert (await Groups.get_groups_by_member_ids(['lark'], db=group_db))['lark'][0].id == 'old'


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['offline', 'malformed', 'duplicate', 'cursor'])
async def test_failed_or_incomplete_snapshot_leaves_mirror_unchanged(group_db, monkeypatch, failure):
    first = {'data': [row()], 'next_cursor': 'next'}
    errors = {
        'offline': group_mirror.SoevApiError(503, 'unavailable', 'Unavailable'),
        'malformed': {'data': [{'id': 'broken'}], 'next_cursor': None},
        'duplicate': {'data': [row()], 'next_cursor': None},
        'cursor': {'data': [], 'next_cursor': 'next'},
    }
    client = SimpleNamespace(get=AsyncMock(side_effect=[first, errors[failure]]))
    monkeypatch.setattr(group_mirror, 'build_service_client', lambda: client)
    with pytest.raises(group_mirror.SoevApiError):
        await group_mirror.reconcile(db=group_db)
    assert (await Groups.get_group_by_id('old', db=group_db)).name == 'Old'
    assert await Groups.get_group_user_ids_by_id('old', db=group_db) == ['fern']


@pytest.mark.asyncio
async def test_empty_snapshot_deletes_stale_groups_and_members(group_db, monkeypatch):
    client = SimpleNamespace(get=AsyncMock(return_value={'data': [], 'next_cursor': None}))
    monkeypatch.setattr(group_mirror, 'build_service_client', lambda: client)
    await group_mirror.reconcile(db=group_db)
    assert await Groups.get_all_groups(db=group_db) == []
    assert (await group_db.scalars(select(GroupMember))).all() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'method,args',
    [
        ('insert_new_group', ['fern', None]),
        ('delete_group_by_id', ['old']),
        ('set_group_user_ids_by_id', ['old', ['lark']]),
        ('delete_all_groups', []),
        ('remove_user_from_all_groups', ['fern']),
        ('create_groups_by_group_names', ['fern', ['Meadow']]),
        ('sync_groups_by_group_names', ['fern', ['Meadow']]),
        ('add_users_to_group', ['old', ['lark']]),
        ('remove_users_from_group', ['old', ['fern']]),
        ('delete_groups_by_user_id', ['fern']),
    ],
)
async def test_model_writers_refuse(group_db, method, args):
    with pytest.raises(HTTPException) as error:
        await getattr(Groups, method)(*args, db=group_db)
    assert error.value.status_code == 403
    assert error.value.detail['code'] == 'platform_groups_read_only'
    assert await Groups.get_group_user_ids_by_id('old', db=group_db) == ['fern']


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'method,path,body',
    [
        ('POST', '/groups/create', {'name': 'Meadow', 'description': ''}),
        ('POST', '/groups/id/old/update', {'name': 'Changed'}),
        ('POST', '/groups/id/old/update', {'description': 'Changed'}),
        ('POST', '/groups/id/old/update', {'user_ids': ['lark']}),
        ('POST', '/groups/id/old/users/add', {'user_ids': ['lark']}),
        ('POST', '/groups/id/old/users/remove', {'user_ids': ['fern']}),
        ('DELETE', '/groups/id/old/delete', None),
        ('POST', '/scim/Groups', {'displayName': 'Meadow'}),
        ('PUT', '/scim/Groups/old', {'displayName': 'Changed', 'members': []}),
        (
            'PATCH',
            '/scim/Groups/old',
            {'Operations': [{'op': 'remove', 'path': 'members'}]},
        ),
        ('DELETE', '/scim/Groups/old', None),
    ],
)
async def test_all_admin_and_scim_mutation_routes_refuse(group_db, method, path, body):
    from open_webui.internal.db import get_async_session
    from open_webui.routers import groups, scim
    from open_webui.utils.auth import get_admin_user, get_verified_user

    app = FastAPI()
    app.include_router(groups.router, prefix='/groups')
    app.include_router(scim.router, prefix='/scim')

    async def session():
        yield group_db

    app.dependency_overrides[get_async_session] = session
    for dependency in (get_admin_user, get_verified_user):
        app.dependency_overrides[dependency] = lambda: SimpleNamespace(id='fern', role='admin')
    app.dependency_overrides[scim.get_scim_auth] = lambda: True
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owui.invalid') as client:
        response = await client.request(method, path, json=body)
        if '/old' in path:
            missing = await client.request(method, path.replace('/old', '/missing'), json=body)
            assert missing.status_code == response.status_code
            assert missing.json() == response.json()
    assert response.status_code == 403, response.text
    assert response.json()['detail']['code'] == 'platform_groups_read_only'
    assert (await Groups.get_group_by_id('old', db=group_db)).name == 'Old'
    assert await Groups.get_group_user_ids_by_id('old', db=group_db) == ['fern']


@pytest.mark.asyncio
async def test_oauth_group_sync_refuses_without_mutating(group_db):
    from open_webui.utils.oauth import OAuthManager

    with pytest.raises(HTTPException) as error:
        await OAuthManager.update_user_groups(
            None,
            None,
            SimpleNamespace(id='fern'),
            {'groups': ['Meadow']},
            {},
            db=group_db,
        )
    assert error.value.status_code == 403
    assert await Groups.get_group_user_ids_by_id('old', db=group_db) == ['fern']


@pytest.mark.asyncio
async def test_product_permissions_are_local_configuration_only(group_db, monkeypatch):
    # The product setting cannot author group identity, membership or platform policy.
    client = SimpleNamespace(send=AsyncMock(side_effect=AssertionError('No platform writes')))
    monkeypatch.setattr(group_mirror, 'build_service_client', lambda: client)
    updated = await Groups.update_group_by_id(
        'old',
        GroupUpdateForm(
            permissions={'platform': {'group:create': True, 'role:assign': True}},
            data={'config': {'share': True}},
        ),
        db=group_db,
    )
    assert updated.permissions['platform']['role:assign'] is True
    with pytest.raises(HTTPException):
        await Groups.insert_new_group('fern', None, db=group_db)
    assert updated.name == 'Old'
    assert await Groups.get_group_user_ids_by_id('old', db=group_db) == ['fern']
    client.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_startup_periodic_and_failure_retry(monkeypatch):
    now = [0.0]
    calls = AsyncMock(side_effect=[RuntimeError('offline'), None, None])
    monkeypatch.setattr(group_mirror, 'reconcile', calls)
    monkeypatch.setattr(group_mirror, 'monotonic', lambda: now[0])
    monkeypatch.setattr(group_mirror.config, 'SOEV_GROUP_RECONCILE_MINUTES', 1)

    async def pause(_):
        now[0] += 10
        if now[0] > 70:
            raise asyncio.CancelledError

    monkeypatch.setattr(group_mirror.asyncio, 'sleep', pause)
    await group_mirror._run(asyncio.Event())
    assert calls.await_count == 3


@pytest.mark.asyncio
async def test_ldap_management_refuses_before_binding_or_writing(group_db, monkeypatch):
    from fastapi import Request, Response
    from open_webui.models.auths import LdapForm
    from open_webui.routers import auths

    monkeypatch.setattr(auths, 'ENABLE_PASSWORD_AUTH', True)
    monkeypatch.setattr(
        auths.Config,
        'get',
        AsyncMock(
            side_effect=lambda key: {
                'ldap.enable': True,
                'ldap.group.enable_management': True,
            }.get(key, '')
        ),
    )
    bind = AsyncMock(side_effect=AssertionError('No LDAP bind'))
    monkeypatch.setattr(auths, 'Connection', bind)
    with pytest.raises(HTTPException) as error:
        await auths.ldap_auth(
            Request({'type': 'http', 'headers': []}),
            Response(),
            LdapForm(user='fern', password='fictional-password'),
            db=group_db,
        )
    assert error.value.status_code == 403
    assert error.value.detail['code'] == 'platform_groups_read_only'
    bind.assert_not_called()
    assert await Groups.get_group_user_ids_by_id('old', db=group_db) == ['fern']


@pytest.mark.asyncio
async def test_default_signup_assignment_does_not_write_platform_members(group_db):
    from open_webui.utils.groups import apply_default_group_assignment

    await apply_default_group_assignment('old', 'lark', db=group_db)
    assert await Groups.get_group_user_ids_by_id('old', db=group_db) == ['fern']


@pytest.mark.asyncio
async def test_admin_group_reads_and_product_setting_update_remain_available(group_db):
    from open_webui.internal.db import get_async_session
    from open_webui.routers import groups
    from open_webui.utils.auth import get_admin_user, get_verified_user

    app = FastAPI()
    app.include_router(groups.router, prefix='/groups')

    async def session():
        yield group_db

    app.dependency_overrides[get_async_session] = session
    for dependency in (get_admin_user, get_verified_user):
        app.dependency_overrides[dependency] = lambda: SimpleNamespace(id='fern', role='admin')
    # Event publishing needs application configuration; isolate that side effect.
    from unittest.mock import patch

    with patch.object(groups, 'publish_event', AsyncMock()):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owui.invalid') as client:
            listing = await client.get('/groups/')
            assert listing.status_code == 200
            assert [g['id'] for g in listing.json()] == ['old']
            members = await client.post('/groups/id/old/users')
            assert members.status_code == 200
            assert [user['id'] for user in members.json()] == ['fern']
            updated = await client.post(
                '/groups/id/old/update',
                json={
                    'permissions': {'chat': {'file_upload': False}},
                    'data': {'config': {'share': False}},
                },
            )
            assert updated.status_code == 200, updated.text
            assert updated.json()['name'] == 'Old'
            assert updated.json()['permissions'] == {'chat': {'file_upload': False}}


@pytest.mark.asyncio
async def test_platform_group_pull_over_real_http(group_db, monkeypatch):
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs, urlsplit

    requests = []

    class Directory(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.command, self.path, self.headers.get('Authorization')))
            query = parse_qs(urlsplit(self.path).query)
            second = query.get('cursor') == ['meadow-page-2']
            body = json.dumps(
                {
                    'data': [row('new', ['platform:user:fern-person'])] if second else [row()],
                    'next_cursor': None if second else 'meadow-page-2',
                }
            ).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Directory)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(group_mirror.config, 'SOEV_API_URL', f'http://127.0.0.1:{server.server_port}')
    try:
        await group_mirror.reconcile(db=group_db)
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
    assert [r[:2] for r in requests] == [
        ('GET', '/v1/groups?limit=200'),
        ('GET', '/v1/groups?limit=200&cursor=meadow-page-2'),
    ]
    assert all(r[2] == 'Bearer soev_test_cred-runtime_test-secret' for r in requests)
    assert await Groups.get_group_user_ids_by_id('old', db=group_db) == ['lark']
    assert await Groups.get_group_user_ids_by_id('new', db=group_db) == ['fern']
