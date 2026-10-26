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


def row(id='13b95f20-57d8-48c6-9a13-042bcf78d021', members=None):
    return {
        'id': id,
        'ref': f'platform:group:{id}',
        'name': 'Meadow',
        'members': [
            'platform:user:70c51d1b-8310-4649-89bd-084764d104f6',
            'platform:user:14f2cf2b-779e-402a-960a-96b4078f081e',
        ]
        if members is None
        else members,
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
            return httpx.Response(
                200,
                json={
                    'data': [
                        row(
                            '624ece59-0d53-46f0-9e2c-a6427ab2e521',
                            ['platform:user:128fdbd4-4697-4cf0-beda-de53b51b8d30'],
                        )
                    ],
                    'next_cursor': None,
                },
            )
        return httpx.Response(200, json={'data': [row()], 'next_cursor': 'page-2'})

    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        'open_webui.soev.client.httpx.AsyncClient',
        lambda **kw: client_type(transport=httpx.MockTransport(handle), **kw),
    )
    await group_mirror.reconcile(db=group_db)
    assert len(requests) == 2
    resolve_token.assert_not_awaited()
    old = await Groups.get_group_by_id('13b95f20-57d8-48c6-9a13-042bcf78d021', db=group_db)
    assert old.name == 'Meadow'
    assert old.permissions == {'workspace': {'knowledge': True}}
    assert old.data == {'config': {'share': 'members'}}
    assert await Groups.get_group_user_ids_by_id('13b95f20-57d8-48c6-9a13-042bcf78d021', db=group_db) == ['lark']
    assert await Groups.get_group_user_ids_by_id('624ece59-0d53-46f0-9e2c-a6427ab2e521', db=group_db) == ['fern']
    assert [g.id for g in await Groups.get_groups_by_member_id('unlinked', db=group_db)] == []
    assert (await Groups.get_groups_by_member_ids(['lark'], db=group_db))['lark'][
        0
    ].id == '13b95f20-57d8-48c6-9a13-042bcf78d021'


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
    assert (await Groups.get_group_by_id('13b95f20-57d8-48c6-9a13-042bcf78d021', db=group_db)).name == 'Old'
    assert await Groups.get_group_user_ids_by_id('13b95f20-57d8-48c6-9a13-042bcf78d021', db=group_db) == ['fern']


@pytest.mark.asyncio
async def test_empty_snapshot_deletes_stale_groups_and_members(group_db, monkeypatch):
    client = SimpleNamespace(get=AsyncMock(return_value={'data': [], 'next_cursor': None}))
    monkeypatch.setattr(group_mirror, 'build_service_client', lambda: client)
    await group_mirror.reconcile(db=group_db)
    assert await Groups.get_all_groups(db=group_db) == []
    assert (await group_db.scalars(select(GroupMember))).all() == []


@pytest.mark.asyncio
@pytest.mark.parametrize('configured', [False, True], ids=['local', 'platform'])
@pytest.mark.parametrize('evidence', ['fresh', 'missing', 'expired'])
async def test_group_authority_depends_on_configuration_and_snapshot(
    group_db, monkeypatch, snapshot_clock, configured, evidence
):
    from open_webui import config
    from open_webui.models.access_grants import AccessGrant, AccessGrantsTable
    from open_webui.models.groups import GroupSnapshot
    from open_webui.utils.access_control import get_permissions
    from sqlalchemy import delete

    group_id = row()['id']
    group_db.add(
        AccessGrant(
            id='meadow-read',
            resource_type='note',
            resource_id='meadow',
            principal_type='group',
            principal_id=group_id,
            permission='read',
            created_at=1,
        )
    )
    if evidence == 'missing':
        await group_db.execute(delete(GroupSnapshot))
    elif evidence == 'expired':
        snapshot_clock[0] += 5
    await group_db.commit()
    if not configured:
        monkeypatch.setattr(config, 'SOEV_API_URL', '')
        monkeypatch.setattr(config, 'SOEV_GROUP_SNAPSHOT_MAX_AGE_SECONDS', None)

    allowed = not configured or evidence == 'fresh'
    assert bool(await Groups.get_groups_by_member_id('fern', db=group_db)) is allowed
    assert await get_permissions('fern', {'workspace': {'knowledge': False}}, db=group_db) == {
        'workspace': {'knowledge': allowed}
    }
    grants = AccessGrantsTable()
    for precomputed in (None, {group_id}):
        assert await grants.has_access('fern', 'note', 'meadow', user_group_ids=precomputed, db=group_db) is allowed
        assert not await grants.has_access('unlinked', 'note', 'meadow', user_group_ids=precomputed, db=group_db)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'method,args',
    [
        ('insert_new_group', ['fern', None]),
        ('delete_group_by_id', ['13b95f20-57d8-48c6-9a13-042bcf78d021']),
        ('set_group_user_ids_by_id', ['13b95f20-57d8-48c6-9a13-042bcf78d021', ['lark']]),
        ('delete_all_groups', []),
        ('remove_user_from_all_groups', ['fern']),
        ('create_groups_by_group_names', ['fern', ['Meadow']]),
        ('sync_groups_by_group_names', ['fern', ['Meadow']]),
        ('add_users_to_group', ['13b95f20-57d8-48c6-9a13-042bcf78d021', ['lark']]),
        ('remove_users_from_group', ['13b95f20-57d8-48c6-9a13-042bcf78d021', ['fern']]),
        ('delete_groups_by_user_id', ['fern']),
    ],
)
async def test_model_writers_refuse(group_db, method, args):
    with pytest.raises(HTTPException) as error:
        await getattr(Groups, method)(*args, db=group_db)
    assert error.value.status_code == 403
    assert error.value.detail['code'] == 'platform_groups_read_only'
    assert await Groups.get_group_user_ids_by_id('13b95f20-57d8-48c6-9a13-042bcf78d021', db=group_db) == ['fern']


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'method,path,body',
    [
        ('POST', '/groups/create', {'name': 'Meadow', 'description': ''}),
        ('POST', '/groups/id/13b95f20-57d8-48c6-9a13-042bcf78d021/update', {'name': 'Changed'}),
        ('POST', '/groups/id/13b95f20-57d8-48c6-9a13-042bcf78d021/update', {'description': 'Changed'}),
        ('POST', '/groups/id/13b95f20-57d8-48c6-9a13-042bcf78d021/update', {'user_ids': ['lark']}),
        ('POST', '/groups/id/13b95f20-57d8-48c6-9a13-042bcf78d021/users/add', {'user_ids': ['lark']}),
        ('POST', '/groups/id/13b95f20-57d8-48c6-9a13-042bcf78d021/users/remove', {'user_ids': ['fern']}),
        ('DELETE', '/groups/id/13b95f20-57d8-48c6-9a13-042bcf78d021/delete', None),
        ('POST', '/scim/Groups', {'displayName': 'Meadow'}),
        ('PUT', '/scim/Groups/13b95f20-57d8-48c6-9a13-042bcf78d021', {'displayName': 'Changed', 'members': []}),
        (
            'PATCH',
            '/scim/Groups/13b95f20-57d8-48c6-9a13-042bcf78d021',
            {'Operations': [{'op': 'remove', 'path': 'members'}]},
        ),
        ('DELETE', '/scim/Groups/13b95f20-57d8-48c6-9a13-042bcf78d021', None),
    ],
)
async def test_all_admin_and_scim_mutation_routes_refuse(group_db, method, path, body):
    import time

    from open_webui.internal.db import get_async_session
    from open_webui.models.groups import GroupSnapshot
    from open_webui.routers import groups, scim
    from open_webui.utils.auth import get_admin_user, get_verified_user

    snapshot = await group_db.get(GroupSnapshot, 'directory')
    snapshot.fetched_at = time.time()
    await group_db.commit()

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
        if '/13b95f20-57d8-48c6-9a13-042bcf78d021' in path:
            missing = await client.request(
                method, path.replace('/13b95f20-57d8-48c6-9a13-042bcf78d021', '/missing'), json=body
            )
            assert missing.status_code == response.status_code
            assert missing.json() == response.json()
    assert response.status_code == 403, response.text
    assert response.json()['detail']['code'] == 'platform_groups_read_only'
    assert (await Groups.get_group_by_id('13b95f20-57d8-48c6-9a13-042bcf78d021', db=group_db)).name == 'Old'
    assert await Groups.get_group_user_ids_by_id('13b95f20-57d8-48c6-9a13-042bcf78d021', db=group_db) == ['fern']


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
    assert await Groups.get_group_user_ids_by_id('13b95f20-57d8-48c6-9a13-042bcf78d021', db=group_db) == ['fern']


@pytest.mark.asyncio
async def test_product_permissions_are_local_configuration_only(group_db, monkeypatch):
    # The product setting cannot author group identity, membership or platform policy.
    client = SimpleNamespace(send=AsyncMock(side_effect=AssertionError('No platform writes')))
    monkeypatch.setattr(group_mirror, 'build_service_client', lambda: client)
    updated = await Groups.update_group_by_id(
        '13b95f20-57d8-48c6-9a13-042bcf78d021',
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
    assert await Groups.get_group_user_ids_by_id('13b95f20-57d8-48c6-9a13-042bcf78d021', db=group_db) == ['fern']
    client.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_startup_periodic_and_failure_retry(monkeypatch):
    calls = AsyncMock(side_effect=[RuntimeError('offline'), None, asyncio.CancelledError])
    monkeypatch.setattr(group_mirror, 'reconcile', calls)
    monkeypatch.setattr(group_mirror, '_pull_due', AsyncMock(return_value=True))
    monkeypatch.setattr(group_mirror.config, 'SOEV_API_URL', 'https://directory.invalid')
    monkeypatch.setattr(group_mirror.config, 'SOEV_GROUP_RECONCILE_MINUTES', 1)
    monkeypatch.setattr(group_mirror.config, 'SOEV_GROUP_SNAPSHOT_MAX_AGE_SECONDS', 5)
    monkeypatch.setattr(group_mirror.asyncio, 'sleep', AsyncMock())
    await group_mirror._run(asyncio.Event())
    assert calls.await_count == 3
    calls.assert_awaited_with(scheduled=True)


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
    assert await Groups.get_group_user_ids_by_id('13b95f20-57d8-48c6-9a13-042bcf78d021', db=group_db) == ['fern']


@pytest.mark.asyncio
async def test_default_signup_assignment_does_not_write_platform_members(group_db):
    from open_webui.utils.groups import apply_default_group_assignment

    await apply_default_group_assignment('13b95f20-57d8-48c6-9a13-042bcf78d021', 'lark', db=group_db)
    assert await Groups.get_group_user_ids_by_id('13b95f20-57d8-48c6-9a13-042bcf78d021', db=group_db) == ['fern']


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
            assert [g['id'] for g in listing.json()] == ['13b95f20-57d8-48c6-9a13-042bcf78d021']
            members = await client.post('/groups/id/13b95f20-57d8-48c6-9a13-042bcf78d021/users')
            assert members.status_code == 200
            assert [user['id'] for user in members.json()] == ['fern']
            updated = await client.post(
                '/groups/id/13b95f20-57d8-48c6-9a13-042bcf78d021/update',
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
                    'data': [
                        row(
                            '624ece59-0d53-46f0-9e2c-a6427ab2e521',
                            ['platform:user:128fdbd4-4697-4cf0-beda-de53b51b8d30'],
                        )
                    ]
                    if second
                    else [row()],
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
    assert await Groups.get_group_user_ids_by_id('13b95f20-57d8-48c6-9a13-042bcf78d021', db=group_db) == ['lark']
    assert await Groups.get_group_user_ids_by_id('624ece59-0d53-46f0-9e2c-a6427ab2e521', db=group_db) == ['fern']


@pytest.mark.asyncio
@pytest.mark.parametrize('state', ['never', 'expired', 'unverified'])
async def test_unverified_snapshots_grant_no_membership(group_db, snapshot_clock, state):

    from open_webui.models import groups as model
    from open_webui.models.access_grants import AccessGrant, AccessGrantsTable
    from open_webui.models.folders import Folders
    from open_webui.models.users import Users
    from open_webui.utils.access_control import has_access
    from sqlalchemy import delete

    snapshot = await group_db.get(model.GroupSnapshot, 'directory')
    if state == 'never':
        await group_db.execute(delete(model.GroupSnapshot))
    elif state == 'expired':
        snapshot_clock[0] += 5
    else:
        snapshot.fetch_started_at = None
    group_db.add_all(
        [
            AccessGrant(
                id='group-grant',
                resource_type='folder',
                resource_id='meadow',
                principal_type='group',
                principal_id='13b95f20-57d8-48c6-9a13-042bcf78d021',
                permission='read',
                created_at=1,
            ),
            AccessGrant(
                id='direct-grant',
                resource_type='folder',
                resource_id='reed',
                principal_type='user',
                principal_id='fern',
                permission='read',
                created_at=1,
            ),
            AccessGrant(
                id='public-grant',
                resource_type='folder',
                resource_id='public',
                principal_type='user',
                principal_id='*',
                permission='read',
                created_at=1,
            ),
        ]
    )
    await group_db.commit()
    grants = AccessGrantsTable()
    assert await model.Groups.get_groups_by_member_id('fern', db=group_db) == []
    assert await model.Groups.get_groups_by_member_ids(['fern'], db=group_db) == {'fern': []}
    assert await model.Groups.get_group_user_ids_by_id('13b95f20-57d8-48c6-9a13-042bcf78d021', db=group_db) == []
    assert await model.Groups.get_group_user_ids_by_ids(['13b95f20-57d8-48c6-9a13-042bcf78d021'], db=group_db) == {
        '13b95f20-57d8-48c6-9a13-042bcf78d021': []
    }
    assert await model.Groups.get_group_member_count_by_id('13b95f20-57d8-48c6-9a13-042bcf78d021', db=group_db) == 0
    assert (
        await model.Groups.get_group_member_counts_by_ids(['13b95f20-57d8-48c6-9a13-042bcf78d021'], db=group_db) == {}
    )
    assert await model.Groups.get_groups({'member_id': 'fern'}, db=group_db) == []
    assert (await model.Groups.search_groups({'member_id': 'fern'}, db=group_db))['total'] == 0
    assert await Users.get_users_by_group_id('13b95f20-57d8-48c6-9a13-042bcf78d021', db=group_db) == []
    for precomputed in (None, {'13b95f20-57d8-48c6-9a13-042bcf78d021'}):
        assert not await grants.has_access('fern', 'folder', 'meadow', user_group_ids=precomputed, db=group_db)
        assert await grants.get_accessible_resource_ids(
            'fern', 'folder', ['meadow', 'reed', 'public'], user_group_ids=precomputed, db=group_db
        ) == {'reed', 'public'}
    assert await Folders.get_shared_folder_ids_for_user(
        'fern', {'13b95f20-57d8-48c6-9a13-042bcf78d021'}, db=group_db
    ) == {
        'reed': 'read',
        'public': 'read',
    }
    assert not await has_access(
        'fern',
        access_grants=[
            {
                'principal_type': 'group',
                'principal_id': '13b95f20-57d8-48c6-9a13-042bcf78d021',
                'permission': 'read',
            }
        ],
        user_group_ids={'13b95f20-57d8-48c6-9a13-042bcf78d021'},
        db=group_db,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize('permission', ['read', 'write', 'read_only'])
async def test_cached_permission_queries_recheck_snapshot_at_execution(group_db, snapshot_clock, permission):

    from open_webui.models import groups as model
    from open_webui.models.access_grants import AccessGrant, AccessGrantsTable

    now = snapshot_clock[0]
    group_db.add(
        AccessGrant(
            id='grant',
            resource_type='user',
            resource_id='lark',
            principal_type='group',
            principal_id='13b95f20-57d8-48c6-9a13-042bcf78d021',
            permission='write' if permission == 'write' else 'read',
            created_at=1,
        )
    )
    await group_db.commit()
    # The resource model needs an owner column; the group table supplies one.
    group_db.add(
        AccessGrant(
            id='group-resource-grant',
            resource_type='group',
            resource_id='13b95f20-57d8-48c6-9a13-042bcf78d021',
            principal_type='group',
            principal_id='13b95f20-57d8-48c6-9a13-042bcf78d021',
            permission='write' if permission == 'write' else 'read',
            created_at=1,
        )
    )
    group = await group_db.get(model.Group, '13b95f20-57d8-48c6-9a13-042bcf78d021')
    group.user_id = 'unlinked'
    await group_db.commit()
    query = AccessGrantsTable().has_permission_filter(
        group_db,
        select(model.Group.id),
        model.Group,
        {'user_id': 'fern', 'group_ids': ['13b95f20-57d8-48c6-9a13-042bcf78d021']},
        'group',
        permission,
    )
    snapshot_clock[0] = now + 4.9
    assert (await group_db.scalars(query)).all() == ['13b95f20-57d8-48c6-9a13-042bcf78d021']
    snapshot_clock[0] = now + 5
    assert (await group_db.scalars(query)).all() == []


@pytest.mark.asyncio
async def test_refresh_revocation_overrides_precomputed_memberships(group_db, monkeypatch):
    from open_webui.models.access_grants import AccessGrant, AccessGrantsTable

    group_db.add(
        AccessGrant(
            id='grant',
            resource_type='model',
            resource_id='meadow',
            principal_type='group',
            principal_id='13b95f20-57d8-48c6-9a13-042bcf78d021',
            permission='read',
            created_at=1,
        )
    )
    await group_db.commit()
    grants = AccessGrantsTable()
    assert await grants.has_access(
        'fern', 'model', 'meadow', user_group_ids={'13b95f20-57d8-48c6-9a13-042bcf78d021'}, db=group_db
    )
    monkeypatch.setattr(
        group_mirror,
        'build_service_client',
        lambda: SimpleNamespace(
            get=AsyncMock(return_value={'data': [row(members=[])], 'next_cursor': None}),
        ),
    )
    await group_mirror.reconcile(db=group_db)
    assert not await grants.has_access(
        'fern', 'model', 'meadow', user_group_ids={'13b95f20-57d8-48c6-9a13-042bcf78d021'}, db=group_db
    )


@pytest.mark.asyncio
async def test_failed_refresh_never_renews_snapshot(group_db, monkeypatch):
    from open_webui.models.groups import GroupSnapshot

    before = await group_db.get(GroupSnapshot, 'directory')
    before_times = before.fetched_at, before.completed_at, before.fetch_started_at
    monkeypatch.setattr(
        group_mirror,
        'build_service_client',
        lambda: SimpleNamespace(
            get=AsyncMock(side_effect=group_mirror.SoevApiError(503, 'unavailable', 'Unavailable')),
        ),
    )
    for _ in range(2):
        with pytest.raises(group_mirror.SoevApiError):
            await group_mirror.reconcile(db=group_db)
        current = await group_db.get(GroupSnapshot, 'directory', populate_existing=True)
        assert (current.fetched_at, current.completed_at, current.fetch_started_at) == before_times


@pytest.mark.asyncio
async def test_snapshot_and_memberships_rollback_together(group_db, monkeypatch):
    from open_webui.models.groups import GroupSnapshot

    before = (await group_db.get(GroupSnapshot, 'directory')).fetched_at
    monkeypatch.setattr(
        group_mirror,
        'build_service_client',
        lambda: SimpleNamespace(
            get=AsyncMock(return_value={'data': [row(members=[])], 'next_cursor': None}),
        ),
    )
    commit = group_db.commit
    attempts = 0

    async def fail_snapshot_commit():
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            raise RuntimeError('fictional commit failure')
        await commit()

    monkeypatch.setattr(group_db, 'commit', fail_snapshot_commit)
    with pytest.raises(RuntimeError):
        await group_mirror.reconcile(db=group_db)
    assert (await group_db.get(GroupSnapshot, 'directory')).fetched_at == before
    assert await Groups.get_group_user_ids_by_id('13b95f20-57d8-48c6-9a13-042bcf78d021', db=group_db) == ['fern']


@pytest.mark.asyncio
async def test_slow_pull_cannot_extend_evidence_validity(group_db, monkeypatch, snapshot_clock):
    from open_webui.models.groups import GroupSnapshot

    async def fetch():
        snapshot_clock[0] += 6
        return {row()['id']: group_mirror.PlatformGroup.model_validate(row())}

    monkeypatch.setattr(group_mirror, '_fetch_groups', fetch)
    before = (await group_db.get(GroupSnapshot, 'directory')).fetch_started_at
    with pytest.raises(RuntimeError, match='validity bound'):
        await group_mirror.reconcile(db=group_db)
    snapshot = await group_db.get(GroupSnapshot, 'directory')
    assert snapshot.fetch_started_at == before
    assert await Groups.get_groups_by_member_id('lark', db=group_db) == []


@pytest.mark.parametrize('value', [None, '0', '4', '6', 'nan'])
def test_snapshot_bound_is_required_and_matches_platform(monkeypatch, value):
    import os
    from pathlib import Path

    from open_webui import config

    if value is None:
        monkeypatch.delenv('SOEV_GROUP_SNAPSHOT_MAX_AGE_SECONDS', raising=False)
    else:
        monkeypatch.setenv('SOEV_GROUP_SNAPSHOT_MAX_AGE_SECONDS', value)
    source = Path(config.__file__).read_text()
    start = source.index('SOEV_GROUP_SNAPSHOT_MAX_AGE_SECONDS =')
    end = source.index('SOEV_GROUP_RECONCILE_MINUTES =', start)
    with pytest.raises(ValueError):
        exec(source[start:end], {'os': os, 'SOEV_API_URL': 'https://soev.invalid'})
    monkeypatch.setenv('SOEV_GROUP_SNAPSHOT_MAX_AGE_SECONDS', '5')
    settings = {'os': os, 'SOEV_API_URL': 'https://soev.invalid'}
    exec(source[start:end], settings)
    assert settings['SOEV_GROUP_SNAPSHOT_MAX_AGE_SECONDS'] == 5


def test_snapshot_migration_starts_unverified_and_downgrades():
    import importlib

    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    migration = importlib.import_module('open_webui.migrations.versions.da27c90f4b61_add_group_snapshot_freshness')
    engine = sa.create_engine('sqlite:///:memory:')
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            assert connection.execute(sa.text('SELECT count(*) FROM soev_group_snapshot')).scalar() == 0
            migration.downgrade()
        assert 'soev_group_snapshot' not in sa.inspect(connection).get_table_names()
    engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'bad',
    [
        {'id': 'meadow'},
        {'id': '13B95F20-57D8-48C6-9A13-042BCF78D021'},
        {'ref': 'platform:group:other'},
        {'members': ['128fdbd4-4697-4cf0-beda-de53b51b8d30']},
        {'members': ['platform:user:fern-person']},
        {'members': ['platform:user:128FDBD4-4697-4CF0-BEDA-DE53B51B8D30']},
        {'members': ['platform:user:128fdbd446974cf0bedade53b51b8d30']},
        {'members': ['platform:user:{128fdbd4-4697-4cf0-beda-de53b51b8d30}']},
    ],
)
async def test_noncanonical_snapshot_refuses_without_replacing_evidence(group_db, monkeypatch, bad):
    from open_webui.models.groups import GroupSnapshot

    before = (await group_db.get(GroupSnapshot, 'directory')).fetched_at
    payload = {**row(), **bad}
    monkeypatch.setattr(
        group_mirror,
        'build_service_client',
        lambda: SimpleNamespace(
            get=AsyncMock(return_value={'data': [payload], 'next_cursor': None}),
        ),
    )
    with pytest.raises(group_mirror.SoevApiError):
        await group_mirror.reconcile(db=group_db)
    assert (await group_db.get(GroupSnapshot, 'directory')).fetched_at == before
    assert await Groups.get_group_user_ids_by_id('13b95f20-57d8-48c6-9a13-042bcf78d021', db=group_db) == ['fern']


@pytest.mark.asyncio
async def test_ambiguous_person_binding_grants_nobody_and_logs_ids(group_db, monkeypatch, caplog):
    import sqlalchemy as sa

    # Reproduce a pre-constraint store without weakening the production schema.
    async with group_db.bind.begin() as connection:
        await connection.execute(sa.text('DROP TABLE user'))
        await connection.execute(sa.text('CREATE TABLE user (id TEXT PRIMARY KEY, person_id TEXT)'))
        await connection.execute(
            sa.text('INSERT INTO user VALUES (:id, :person)'),
            [
                {'id': 'fern', 'person': 'platform:user:128fdbd4-4697-4cf0-beda-de53b51b8d30'},
                {'id': 'lark', 'person': 'platform:user:128fdbd4-4697-4cf0-beda-de53b51b8d30'},
                {'id': 'reed', 'person': '128fdbd4-4697-4cf0-beda-de53b51b8d30'},
            ],
        )
    monkeypatch.setattr(
        group_mirror,
        'build_service_client',
        lambda: SimpleNamespace(
            get=AsyncMock(
                return_value={
                    'data': [row(members=['platform:user:128fdbd4-4697-4cf0-beda-de53b51b8d30'])],
                    'next_cursor': None,
                }
            ),
        ),
    )
    await group_mirror.reconcile(db=group_db)
    assert await Groups.get_group_user_ids_by_id('13b95f20-57d8-48c6-9a13-042bcf78d021', db=group_db) == []
    record = next(r for r in caplog.records if r.message == 'ambiguous person binding')
    assert record.user_ids == ['fern', 'lark']
    assert 'example.invalid' not in caplog.text
    # A remaining raw UUID binding must never match a canonical member ref.
    await group_db.execute(sa.text("DELETE FROM user WHERE id IN ('fern', 'lark')"))
    await group_db.commit()
    await group_mirror.reconcile(db=group_db)
    assert await Groups.get_group_user_ids_by_id('13b95f20-57d8-48c6-9a13-042bcf78d021', db=group_db) == []


@pytest.mark.asyncio
@pytest.mark.parametrize('role', ['user', 'admin'])
async def test_directory_read_scope_and_hidden_group_differential(group_db, role):
    import time

    from open_webui.internal.db import get_async_session
    from open_webui.models.groups import Group, GroupSnapshot
    from open_webui.routers import groups, users
    from open_webui.utils.auth import get_admin_user, get_verified_user

    visible = '13b95f20-57d8-48c6-9a13-042bcf78d021'
    hidden = '624ece59-0d53-46f0-9e2c-a6427ab2e521'
    group_db.add(
        Group(
            id=hidden,
            user_id='',
            name='Hidden grove',
            description='',
            created_at=1,
            updated_at=2,
            data={'config': {'share': True}},
        )
    )
    group_db.add_all(
        [
            GroupMember(id='shared-lark', group_id=visible, user_id='lark', created_at=1, updated_at=1),
            GroupMember(id='hidden-lark', group_id=hidden, user_id='lark', created_at=1, updated_at=1),
        ]
    )
    snapshot = await group_db.get(GroupSnapshot, 'directory')
    snapshot.fetched_at = time.time()
    await group_db.commit()
    app = FastAPI()
    app.include_router(groups.router, prefix='/groups')
    app.include_router(users.router, prefix='/users')

    async def session():
        yield group_db

    async def verified():
        return SimpleNamespace(id='fern', role=role)

    async def admin():
        if role != 'admin':
            raise HTTPException(status_code=401, detail='Admin required')
        return await verified()

    app.dependency_overrides[get_async_session] = session
    app.dependency_overrides[get_verified_user] = verified
    app.dependency_overrides[get_admin_user] = admin
    paths = ['/groups/', '/groups/?share=true', '/groups/?share=false', '/users/lark/info']
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owui.invalid') as client:
        listing = await client.get('/groups/')
        assert listing.status_code == 200
        expected = {visible, hidden} if role == 'admin' else {visible}
        assert {g['id'] for g in listing.json()} == expected
        info = await client.get('/users/lark/info')
        assert {g['id'] for g in info.json()['groups']} == expected
        # The upstream user-info read already supplies this field to this caller.
        assert info.json()['email'] == 'lark@example.invalid'
        assert (await client.get(f'/groups/id/{visible}/info')).json()['member_count'] == 2
        for suffix, method in [('info', 'GET'), ('export', 'GET'), ('users', 'POST')]:
            response = await client.request(method, f'/groups/id/{hidden}/{suffix}')
            if role == 'user':
                missing = await client.request(method, f'/groups/id/missing/{suffix}')
                assert (response.status_code, response.content) == (missing.status_code, missing.content)
                assert response.status_code in (401, 404)
            else:
                assert response.status_code == 200
                if suffix == 'users':
                    assert [member['email'] for member in response.json()] == ['lark@example.invalid']
        baseline = [(await client.get(path)).json() for path in paths]
        if role == 'user':
            group = await group_db.get(Group, hidden)
            group.name = 'Another hidden grove'
            group.data = {'config': {'share': False}}
            group_db.add(GroupMember(id='hidden-reed', group_id=hidden, user_id='unlinked', created_at=1, updated_at=1))
            await group_db.commit()
            assert [(await client.get(path)).json() for path in paths] == baseline
            for share in (True, False):
                result = await Groups.search_groups({'member_id': 'fern', 'share': share}, db=group_db)
                assert {item.id for item in result['items']} <= {visible}
                assert result['total'] == len(result['items'])


@pytest.mark.asyncio
async def test_expired_snapshot_hides_group_info_and_user_memberships(group_db, snapshot_clock):

    from open_webui.routers import groups, users

    snapshot_clock[0] += 5
    await group_db.commit()
    caller = SimpleNamespace(id='fern', role='user')
    for id in ['13b95f20-57d8-48c6-9a13-042bcf78d021', 'missing']:
        with pytest.raises(HTTPException) as error:
            await groups.get_group_info_by_id(id, user=caller, db=group_db)
        assert error.value.status_code == 404
    assert await groups.get_groups(user=caller, db=group_db) == []
    assert (await users.get_user_info_by_id('fern', user=caller, db=group_db)).groups == []


@pytest.mark.asyncio
async def test_event_targets_recheck_precomputed_membership(group_db, snapshot_clock):
    from unittest.mock import patch

    from open_webui.events import event_target_matches

    target = [{'type': 'group', 'id': '13b95f20-57d8-48c6-9a13-042bcf78d021'}]
    cached = {'fern': {'13b95f20-57d8-48c6-9a13-042bcf78d021'}}
    original = Groups.get_groups_by_member_ids

    async def lookup(user_ids):
        return await original(user_ids, db=group_db)

    with patch.object(Groups, 'get_groups_by_member_ids', lookup):
        assert await event_target_matches(target, {'fern'}, cached)
        snapshot_clock[0] += 5
        await group_db.commit()
        assert not await event_target_matches(target, {'fern'}, cached)
        assert await event_target_matches([{'type': 'user', 'id': 'fern'}], {'fern'}, cached)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [False, True])
async def test_refresh_and_retry_cadence_respect_snapshot_bound(group_db, monkeypatch, snapshot_clock, failure):
    from open_webui.models import groups as model

    now = snapshot_clock
    attempted_at = []
    monkeypatch.setattr(group_mirror.config, 'SOEV_GROUP_RECONCILE_MINUTES', 15)
    monkeypatch.setattr(group_mirror.config, 'SOEV_GROUP_SNAPSHOT_MAX_AGE_SECONDS', 5)

    async def fetch():
        attempted_at.append(now[0])
        now[0] += 1  # Pull latency must not shift the next start by a full interval.
        if failure:
            raise RuntimeError('fictional unavailable directory')
        return {row()['id']: group_mirror.PlatformGroup.model_validate(row())}

    async def pause(seconds):
        now[0] += seconds
        if now[0] > 108:
            raise asyncio.CancelledError
        if not failure:
            assert await group_db.scalar(select(model.snapshot_is_fresh()))

    reconcile, observe = group_mirror.reconcile, group_mirror._pull_due
    monkeypatch.setattr(group_mirror, '_fetch_groups', fetch)
    monkeypatch.setattr(group_mirror, 'reconcile', lambda **kwargs: reconcile(db=group_db, **kwargs))
    monkeypatch.setattr(group_mirror, '_pull_due', lambda: observe(db=group_db))
    monkeypatch.setattr(group_mirror.asyncio, 'sleep', pause)
    await group_mirror._run(asyncio.Event())
    assert len(attempted_at) >= 4
    assert all(5 / 3 <= after - before < 5 / 3 + 0.11 for before, after in zip(attempted_at, attempted_at[1:]))
    if failure:
        assert not await group_db.scalar(select(model.snapshot_is_fresh()))


@pytest.mark.asyncio
async def test_tool_write_access_rechecks_membership_after_list_read(group_db, monkeypatch, snapshot_clock):

    from open_webui.models.access_grants import AccessGrant
    from open_webui.models.tools import Tool, Tools
    from open_webui.routers import tools

    async with group_db.bind.begin() as connection:
        await connection.run_sync(Tool.__table__.create)
    group_db.add(
        Tool(
            id='meadow',
            user_id='unlinked',
            name='Meadow',
            content='',
            specs=[],
            meta={},
            created_at=1,
            updated_at=1,
        )
    )
    group_db.add_all(
        [
            AccessGrant(
                id='tool-public',
                resource_type='tool',
                resource_id='meadow',
                principal_type='user',
                principal_id='*',
                permission='read',
                created_at=1,
            ),
            AccessGrant(
                id='tool-writers',
                resource_type='tool',
                resource_id='meadow',
                principal_type='group',
                principal_id='13b95f20-57d8-48c6-9a13-042bcf78d021',
                permission='write',
                created_at=1,
            ),
        ]
    )
    await group_db.commit()
    monkeypatch.setattr(tools, 'ENABLE_PLUGINS', True)
    monkeypatch.setattr(tools, 'can_bypass_access_control', lambda user: False)
    caller = SimpleNamespace(id='fern', role='user')
    assert (await tools.get_tool_list(user=caller, db=group_db))[0].write_access
    original = Tools.get_tools

    async def read_then_expire(**kwargs):
        result = await original(**kwargs)
        snapshot_clock[0] += 5
        await group_db.commit()
        return result

    monkeypatch.setattr(Tools, 'get_tools', read_then_expire)
    listed = await tools.get_tool_list(user=caller, db=group_db)
    assert len(listed) == 1
    assert not listed[0].write_access


@pytest.mark.asyncio
async def test_startup_uses_database_freshness_without_worker_baseline(group_db, monkeypatch):
    from open_webui.models import groups as model

    assert await Groups.get_groups_by_member_id('fern', db=group_db)
    cached_query = select(model.snapshot_is_fresh())
    assert await group_db.scalar(cached_query)

    async def wait_for_stop(stop):
        await stop.wait()

    monkeypatch.setattr(group_mirror, '_run', wait_for_stop)
    app = SimpleNamespace(state=SimpleNamespace())
    group_mirror.start_reconciler(app)
    try:
        assert await group_db.scalar(cached_query)
        assert await Groups.get_groups_by_member_id('fern', db=group_db)
        monkeypatch.setattr(
            group_mirror,
            'build_service_client',
            lambda: SimpleNamespace(
                get=AsyncMock(return_value={'data': [row()], 'next_cursor': None}),
            ),
        )
        await group_mirror.reconcile(db=group_db)
        assert await Groups.get_groups_by_member_id('lark', db=group_db)
    finally:
        await group_mirror.stop_reconciler(app)


@pytest.mark.asyncio
async def test_backward_wall_clock_never_revives_or_extends_snapshot(group_db, monkeypatch, snapshot_clock):
    from open_webui.models import groups as model

    cached = select(model.snapshot_is_fresh())
    assert await group_db.scalar(cached)
    monkeypatch.setattr(group_mirror.time, 'time', lambda: -10000)
    snapshot_clock[0] += 4.9
    assert await group_db.scalar(cached)
    snapshot_clock[0] += 0.1
    assert not await group_db.scalar(cached)
    await group_mirror._pull_due(db=group_db)
    assert not await group_db.scalar(cached)
    monkeypatch.setattr(group_mirror.time, 'time', lambda: 0)
    await group_mirror._pull_due(db=group_db)
    assert not await group_db.scalar(cached)


@pytest.mark.asyncio
async def test_delayed_worker_cannot_revive_expired_database_evidence(group_db, snapshot_clock):
    from open_webui.models import groups as model
    from sqlalchemy.ext.asyncio import async_sessionmaker

    sessions = async_sessionmaker(group_db.bind, expire_on_commit=False)
    snapshot_clock[0] += 6
    async with sessions() as follower:
        for _ in range(3):
            await group_mirror._pull_due(db=follower)
            assert not await follower.scalar(select(model.snapshot_is_fresh()))
            assert await Groups.get_groups_by_member_id('fern', db=follower) == []


@pytest.mark.asyncio
async def test_unchanged_snapshot_refreshes_validity_without_rewriting_memberships(
    group_db, monkeypatch, snapshot_clock
):
    from open_webui.models import groups as model
    from sqlalchemy import event

    monkeypatch.setattr(
        group_mirror,
        '_fetch_groups',
        AsyncMock(return_value={row()['id']: group_mirror.PlatformGroup.model_validate(row())}),
    )
    await group_mirror.reconcile(db=group_db)
    before = (await group_db.execute(select(GroupMember.id, GroupMember.created_at))).all()
    started = await group_db.scalar(select(model.GroupSnapshot.fetch_started_at))
    statements = []
    event.listen(group_db.bind.sync_engine, 'before_cursor_execute', lambda *args: statements.append(args[2]))
    snapshot_clock[0] += 4
    await group_mirror.reconcile(db=group_db)
    assert await group_db.scalar(select(model.GroupSnapshot.fetch_started_at)) == started + 4
    assert (await group_db.execute(select(GroupMember.id, GroupMember.created_at))).all() == before
    assert not any(
        statement.startswith(
            ('INSERT INTO group_member', 'DELETE FROM group_member', 'UPDATE group_member', 'UPDATE "group"')
        )
        for statement in statements
    )
    snapshot_clock[0] += 4
    assert await Groups.get_groups_by_member_id('lark', db=group_db)


@pytest.mark.asyncio
async def test_shared_lease_deduplicates_pulls_and_recovers_after_expiry(group_db, monkeypatch, snapshot_clock):
    from open_webui.models import groups as model
    from sqlalchemy import update
    from sqlalchemy.ext.asyncio import async_sessionmaker

    now = snapshot_clock
    sessions = async_sessionmaker(group_db.bind, expire_on_commit=False)
    other_worker_observed = []

    async def fetch():
        async with sessions() as other:
            # Simulate another process, which has its own asyncio lock.
            monkeypatch.setattr(group_mirror, '_lock', None)
            await group_mirror.reconcile(db=other, scheduled=True)
            other_worker_observed.append(await group_mirror._pull_due(db=other))
        return {row()['id']: group_mirror.PlatformGroup.model_validate(row())}

    fetch_mock = AsyncMock(side_effect=fetch)
    monkeypatch.setattr(group_mirror, '_fetch_groups', fetch_mock)
    await group_mirror.reconcile(db=group_db, scheduled=True)
    assert fetch_mock.await_count == 1
    assert other_worker_observed == [False]
    await group_mirror.reconcile(db=group_db, scheduled=True)
    assert fetch_mock.await_count == 1
    # A dead process's outstanding lease blocks pulls, then expires.
    await group_db.execute(
        update(model.GroupSnapshot).values(lease_token='fictional-dead-worker', lease_until=now[0] + 5 / 3)
    )
    await group_db.commit()
    now[0] += 1.6
    await group_mirror.reconcile(db=group_db, scheduled=True)
    assert fetch_mock.await_count == 1
    now[0] += 0.067
    await group_mirror.reconcile(db=group_db, scheduled=True)
    assert fetch_mock.await_count == 2
    assert await Groups.get_groups_by_member_id('lark', db=group_db)


def test_generation_migration_preserves_evidence_but_starts_unverified():
    import importlib

    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    old = importlib.import_module('open_webui.migrations.versions.da27c90f4b61_add_group_snapshot_freshness')
    migration = importlib.import_module('open_webui.migrations.versions.c7e4a9b26d10_group_snapshot_generations')
    engine = sa.create_engine('sqlite:///:memory:')
    with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
        old.upgrade()
        connection.execute(sa.text("INSERT INTO soev_group_snapshot VALUES ('directory', 10, 11)"))
        migration.upgrade()
        row = connection.execute(
            sa.text('SELECT fetched_at, completed_at, generation, lease_until FROM soev_group_snapshot')
        ).one()
        assert tuple(row) == (10, 11, 0, 0)
        migration.downgrade()
        assert {column['name'] for column in sa.inspect(connection).get_columns('soev_group_snapshot')} == {
            'id',
            'fetched_at',
            'completed_at',
        }
    engine.dispose()


async def _connect_note_worker(manager, worker, session_users, delivered, monkeypatch, publish, transport):
    import socketio
    from open_webui.socket import main as sockets

    server = socketio.AsyncServer(client_manager=manager, async_mode='asgi')
    server.manager_initialized = True
    if transport == 'redis':
        monkeypatch.setattr(manager, '_publish', publish)
    monkeypatch.setattr(server, '_send_eio_packet', AsyncMock(side_effect=lambda sid, packet: delivered.append(sid)))

    async def get_session(sid, namespace=None):
        return {'user': session_users[sid]}

    monkeypatch.setattr(server, 'get_session', get_session)
    for user_id in ('lark', 'fern', 'unlinked'):
        sid = await manager.connect(f'{worker}:{user_id}', '/')
        session_users[sid] = {'id': user_id, 'role': 'user'}
        for joined_room in ('note:meadow', 'doc_note:meadow'):
            await manager.enter_room(sid, '/', joined_room)
        await sockets.YDOC_MANAGER.add_user('note:meadow', sid)


async def _note_broadcast_setup(group_db, monkeypatch, transport):
    from open_webui.internal import db as database
    from open_webui.models.access_grants import AccessGrant
    from open_webui.models.notes import Note, PinnedNote
    from open_webui.models.users import User
    from open_webui.socket import main as sockets
    from open_webui.socket.utils import YdocManager
    from sqlalchemy.ext.asyncio import async_sessionmaker

    async with group_db.bind.begin() as connection:
        await connection.run_sync(Note.__table__.create)
        await connection.run_sync(PinnedNote.__table__.create)
    for user_id in ('fern', 'unlinked'):
        (await group_db.get(User, user_id)).role = 'user'
    group_db.add(Note(id='meadow', user_id='lark', title='Meadow notes', created_at=1, updated_at=1))
    group_db.add_all(
        [
            AccessGrant(
                id='note-group',
                resource_type='note',
                resource_id='meadow',
                principal_type='group',
                principal_id=row()['id'],
                permission='read',
                created_at=1,
            ),
            AccessGrant(
                id='note-direct',
                resource_type='note',
                resource_id='meadow',
                principal_type='user',
                principal_id='unlinked',
                permission='read',
                created_at=1,
            ),
        ]
    )
    await group_db.commit()
    monkeypatch.setattr(database, 'AsyncSessionLocal', async_sessionmaker(group_db.bind, expire_on_commit=False))
    monkeypatch.setattr(sockets, 'YDOC_MANAGER', YdocManager(redis=None, redis_key_prefix='fictional-note-test'))
    delivered, managers = [], []
    session_users = {}

    # Redis fanout enters _handle_emit on each worker. Use the real subscriber path,
    # replacing only Redis transport with an in-memory publication between two managers.
    async def publish(message):
        for manager in managers:
            if manager.host_id != message['host_id']:
                await manager._handle_emit(message)

    for worker in range(2 if transport == 'redis' else 1):
        manager = (
            sockets.NoteRedisManager('redis://fictional.invalid/0')
            if transport == 'redis'
            else sockets.NoteAsyncManager()
        )
        managers.append(manager)
        await _connect_note_worker(manager, worker, session_users, delivered, monkeypatch, publish, transport)
    return SimpleNamespace(managers=managers, delivered=delivered, session_users=session_users)


async def _send_note_broadcast(group_db, monkeypatch, managers, session_users, event, room):
    payload = {'id': 'meadow'} if event == 'events:note' else {'document_id': 'note:meadow', 'update': [1]}
    if event == 'http:update':
        from open_webui.models.notes import NoteForm
        from open_webui.models.users import Users
        from open_webui.routers import notes

        grants = [
            {'principal_type': 'group', 'principal_id': row()['id'], 'permission': 'read'},
            {'principal_type': 'user', 'principal_id': 'unlinked', 'permission': 'read'},
        ]
        monkeypatch.setattr(notes, 'sio', managers[0].server)
        monkeypatch.setattr(notes.Config, 'get', AsyncMock(return_value={}))
        monkeypatch.setattr(notes, 'has_permission', AsyncMock(return_value=True))
        monkeypatch.setattr(notes, 'filter_allowed_access_grants', AsyncMock(return_value=grants))
        monkeypatch.setattr(notes, 'publish_event', AsyncMock())
        owner = await Users.get_user_by_id('lark', db=group_db)

    if event == 'http:update':
        result = await notes.update_note_by_id(
            SimpleNamespace(), 'meadow', NoteForm(title='Meadow update', access_grants=grants), owner, group_db
        )
        assert result.title == 'Meadow update'
    elif room == 'direct':
        for manager in managers:
            for sid, user in list(session_users.items()):
                if sid in manager.rooms.get('/', {}).get(None, {}) and user['id'] == 'fern':
                    await managers[0].server.emit(event, payload, to=sid)
    else:
        await managers[0].server.emit(event, payload, to=room)


@pytest.mark.asyncio
@pytest.mark.parametrize('transport', ['local', 'redis'])
@pytest.mark.parametrize('revocation', ['membership', 'expiry'])
@pytest.mark.parametrize(
    'event,room',
    [
        ('http:update', 'note:meadow'),
        ('events:note', 'note:meadow'),
        ('ydoc:document:update', 'doc_note:meadow'),
        ('ydoc:awareness:update', 'doc_note:meadow'),
        ('ydoc:user:joined', 'doc_note:meadow'),
        ('ydoc:user:left', 'doc_note:meadow'),
        ('ydoc:document:state', 'direct'),
    ],
)
async def test_note_broadcast_revalidates_each_recipient(
    group_db, monkeypatch, snapshot_clock, transport, revocation, event, room
):
    from open_webui.socket import main as sockets
    from sqlalchemy import delete

    setup = await _note_broadcast_setup(group_db, monkeypatch, transport)
    managers, delivered, session_users = setup.managers, setup.delivered, setup.session_users
    await _send_note_broadcast(group_db, monkeypatch, managers, session_users, event, room)
    assert any(sid.endswith(':fern') for sid in delivered)
    delivered.clear()
    if revocation == 'membership':
        await group_db.execute(delete(GroupMember).where(GroupMember.user_id == 'fern'))
        await group_db.commit()
    else:
        snapshot_clock[0] += 5
    await _send_note_broadcast(group_db, monkeypatch, managers, session_users, event, room)
    assert not any(sid.endswith(':fern') for sid in delivered)
    if room != 'direct':
        assert set(delivered) == {
            f'{worker}:{user}' for worker in range(len(managers)) for user in ('lark', 'unlinked')
        }
    for manager in managers:
        for joined_room in ('note:meadow', 'doc_note:meadow'):
            assert all(session_users[sid]['id'] != 'fern' for sid in manager.rooms.get('/', {}).get(joined_room, {}))
    assert all(session_users[sid]['id'] != 'fern' for sid in await sockets.YDOC_MANAGER.get_users('note:meadow'))


@pytest.mark.asyncio
async def test_lease_loss_prevents_stale_snapshot_publication(group_db, monkeypatch):
    from open_webui.models import groups as model
    from sqlalchemy import update
    from sqlalchemy.ext.asyncio import async_sessionmaker

    sessions = async_sessionmaker(group_db.bind, expire_on_commit=False)

    async def fetch_after_takeover():
        async with sessions() as other:
            await other.execute(update(model.GroupSnapshot).values(lease_token='fictional-successor'))
            await other.commit()
        return {row()['id']: group_mirror.PlatformGroup.model_validate(row())}

    monkeypatch.setattr(group_mirror, '_fetch_groups', fetch_after_takeover)
    with pytest.raises(RuntimeError, match='lease or validity bound'):
        await group_mirror.reconcile(db=group_db)
    assert await group_db.scalar(select(model.GroupSnapshot.fetch_started_at)) == 100
    assert await group_db.scalar(select(model.GroupSnapshot.lease_token)) == 'fictional-successor'
    assert await Groups.get_group_user_ids_by_id(row()['id'], db=group_db) == ['fern']


@pytest.mark.asyncio
async def test_disabled_directory_reconciler_stays_idle(monkeypatch):
    monkeypatch.setattr(group_mirror.config, 'SOEV_API_URL', '')
    monkeypatch.setattr(group_mirror.config, 'SOEV_GROUP_SNAPSHOT_MAX_AGE_SECONDS', None)
    observe, reconcile = AsyncMock(), AsyncMock()
    monkeypatch.setattr(group_mirror, '_pull_due', observe)
    monkeypatch.setattr(group_mirror, 'reconcile', reconcile)
    await group_mirror._run(asyncio.Event())
    observe.assert_not_awaited()
    reconcile.assert_not_awaited()


def test_database_clock_sql_uses_live_postgresql_time(identity_config):
    from open_webui.models.groups import snapshot_is_fresh
    from sqlalchemy.dialects import postgresql

    sql = str(select(snapshot_is_fresh()).compile(dialect=postgresql.dialect()))
    assert 'clock_timestamp()' in sql
    assert 'fetch_started_at' in sql
    assert 'CURRENT_TIMESTAMP' not in sql


def test_database_clock_migration_invalidates_worker_evidence():
    import importlib

    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    initial = importlib.import_module('open_webui.migrations.versions.da27c90f4b61_add_group_snapshot_freshness')
    generations = importlib.import_module('open_webui.migrations.versions.c7e4a9b26d10_group_snapshot_generations')
    clock = importlib.import_module('open_webui.migrations.versions.e2f6a1c93d84_group_snapshot_database_clock')
    engine = sa.create_engine('sqlite:///:memory:')
    with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
        initial.upgrade()
        generations.upgrade()
        connection.execute(
            sa.text("UPDATE soev_group_snapshot SET fetched_at=100, generation=7, lease_token='rook', lease_until=999")
        )
        clock.upgrade()
        evidence = connection.execute(
            sa.text(
                'SELECT fetch_started_at, pull_started_at, lease_token, lease_until, next_pull_at '
                'FROM soev_group_snapshot'
            )
        ).one()
        assert tuple(evidence) == (None, None, None, 0, 0)
        assert not {'generation', 'pull_seconds'} & {
            column['name'] for column in sa.inspect(connection).get_columns('soev_group_snapshot')
        }
        clock.downgrade()
        assert connection.execute(sa.text('SELECT generation, pull_seconds FROM soev_group_snapshot')).one() == (0, 0)
    engine.dispose()


@pytest.mark.asyncio
async def test_claim_records_database_start_before_network_without_renewing_evidence(
    group_db, monkeypatch, snapshot_clock
):
    from open_webui.models.groups import GroupSnapshot
    from sqlalchemy.ext.asyncio import async_sessionmaker

    snapshot_clock[0] += 4
    sessions = async_sessionmaker(group_db.bind, expire_on_commit=False)

    async def fetch():
        async with sessions() as follower:
            snapshot = await follower.get(GroupSnapshot, 'directory')
            assert snapshot.pull_started_at == 104
            assert snapshot.fetch_started_at == 100
            assert snapshot.lease_until == pytest.approx(104 + 5 / 3)
            assert snapshot.next_pull_at == pytest.approx(104 + 5 / 3)
            snapshot_clock[0] += 1
            assert await Groups.get_groups_by_member_id('fern', db=follower) == []
        return {row()['id']: group_mirror.PlatformGroup.model_validate(row())}

    monkeypatch.setattr(group_mirror, '_fetch_groups', fetch)
    await group_mirror.reconcile(db=group_db)
    assert await group_db.scalar(select(GroupSnapshot.fetch_started_at)) == 104
    snapshot_clock[0] = 109
    assert await Groups.get_groups_by_member_id('lark', db=group_db) == []


@pytest.mark.asyncio
async def test_heartbeat_keeps_live_puller_past_initial_lease(group_db, monkeypatch, snapshot_clock):
    from open_webui.models.groups import GroupSnapshot
    from sqlalchemy.ext.asyncio import async_sessionmaker

    renewed = asyncio.Event()
    renew = group_mirror._renew_lease
    renewals = []

    async def track_renewal(session, token):
        snapshot_clock[0] += 0.6
        await renew(session, token)
        renewals.append(snapshot_clock[0])
        if len(renewals) == 3:
            renewed.set()

    async def fetch():
        await asyncio.wait_for(renewed.wait(), timeout=3)
        sessions = async_sessionmaker(group_db.bind, expire_on_commit=False)
        async with sessions() as contender:
            monkeypatch.setattr(group_mirror, '_lock', None)
            await group_mirror.reconcile(db=contender, scheduled=True)
        return {row()['id']: group_mirror.PlatformGroup.model_validate(row())}

    fetch_mock = AsyncMock(side_effect=fetch)
    monkeypatch.setattr(group_mirror, '_renew_lease', track_renewal)
    monkeypatch.setattr(group_mirror, '_fetch_groups', fetch_mock)
    await group_mirror.reconcile(db=group_db)
    assert len(renewals) == 3
    assert renewals[-1] > 100 + 5 / 3
    assert fetch_mock.await_count == 1
    assert await group_db.scalar(select(GroupSnapshot.fetch_started_at)) == 100
    assert await Groups.get_groups_by_member_id('lark', db=group_db)


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['claim', 'fetch', 'replace'])
async def test_pull_timeouts_bound_database_and_network_waits(group_db, monkeypatch, phase):
    from open_webui.models.groups import GroupSnapshot

    monkeypatch.setattr(group_mirror.config, 'SOEV_GROUP_SNAPSHOT_MAX_AGE_SECONDS', 0.15)
    cancelled = asyncio.Event()

    async def block(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    fetch = AsyncMock(return_value={row()['id']: group_mirror.PlatformGroup.model_validate(row())})
    monkeypatch.setattr(group_mirror, '_fetch_groups', fetch)
    if phase == 'claim':
        original = group_db.scalar

        async def stall_claim(statement, *args, **kwargs):
            if statement.is_update:
                await block()
            return await original(statement, *args, **kwargs)

        monkeypatch.setattr(group_db, 'scalar', stall_claim)
    elif phase == 'fetch':
        monkeypatch.setattr(group_mirror, '_fetch_groups', block)
    else:
        monkeypatch.setattr(group_mirror, '_replace_snapshot', block)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(group_mirror.reconcile(db=group_db), timeout=1)
    assert cancelled.is_set()
    assert await group_db.scalar(select(GroupSnapshot.fetch_started_at)) == 100
    assert await group_db.scalar(select(GroupSnapshot.lease_token)) is None
    assert (await group_db.execute(select(GroupMember.user_id))).scalars().all() == ['fern']


@pytest.mark.asyncio
@pytest.mark.parametrize('elapsed', [2, 5])
async def test_expiry_during_replacement_rolls_back_memberships_and_evidence(
    group_db, monkeypatch, snapshot_clock, elapsed
):
    from open_webui.models.groups import GroupSnapshot

    replace = group_mirror._replace_snapshot

    async def expire_after_writes(session, groups):
        await replace(session, groups)
        snapshot_clock[0] += elapsed

    monkeypatch.setattr(group_mirror, '_replace_snapshot', expire_after_writes)
    monkeypatch.setattr(
        group_mirror,
        '_fetch_groups',
        AsyncMock(return_value={row()['id']: group_mirror.PlatformGroup.model_validate(row())}),
    )
    with pytest.raises(RuntimeError, match='validity bound'):
        await group_mirror.reconcile(db=group_db)
    assert await group_db.scalar(select(GroupSnapshot.fetch_started_at)) == 100
    assert (await group_db.scalars(select(GroupMember.user_id))).all() == ['fern']


@pytest.mark.asyncio
async def test_superseded_puller_cannot_overwrite_successor_snapshot(group_db, monkeypatch, snapshot_clock):
    from open_webui.models.groups import GroupSnapshot
    from sqlalchemy.ext.asyncio import async_sessionmaker

    sessions = async_sessionmaker(group_db.bind, expire_on_commit=False)
    pulls = 0

    async def fetch():
        nonlocal pulls
        pulls += 1
        if pulls == 1:
            snapshot_clock[0] += 5 / 3 + 0.001
            async with sessions() as successor:
                monkeypatch.setattr(group_mirror, '_lock', None)
                await group_mirror.reconcile(db=successor, scheduled=True)
            return {row()['id']: group_mirror.PlatformGroup.model_validate(row())}
        return {row()['id']: group_mirror.PlatformGroup.model_validate(row(members=[]))}

    monkeypatch.setattr(group_mirror, '_fetch_groups', fetch)
    with pytest.raises(RuntimeError, match='lease or validity bound'):
        await group_mirror.reconcile(db=group_db)
    assert pulls == 2
    assert await group_db.scalar(select(GroupSnapshot.fetch_started_at)) == pytest.approx(snapshot_clock[0], abs=0.001)
    assert (await group_db.scalars(select(GroupMember.user_id))).all() == []


@pytest.mark.asyncio
async def test_heartbeat_lease_loss_cancels_network_fetch(group_db, monkeypatch, snapshot_clock):
    cancelled = asyncio.Event()
    renew = group_mirror._renew_lease

    async def lose_lease(session, token):
        snapshot_clock[0] += 2
        await renew(session, token)

    async def fetch():
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(group_mirror, '_renew_lease', lose_lease)
    monkeypatch.setattr(group_mirror, '_fetch_groups', fetch)
    with pytest.raises(RuntimeError, match='lease or validity bound'):
        await group_mirror.reconcile(db=group_db)
    assert cancelled.is_set()
    assert (await group_db.scalars(select(GroupMember.user_id))).all() == ['fern']
