"""Grant lifecycle, permission composition, inherited access, and authentication."""

from copy import deepcopy

import pytest
from sqlalchemy import update

from .test_matrix import GRANTS, PRINCIPALS, PUBLIC, seed_resource


@pytest.mark.parametrize('path', ('/api/v1/groups/id/readers', '/api/v1/users/owner'))
@pytest.mark.parametrize('principal', (*PRINCIPALS, 'pending', None, 'invalid'))
def test_administration_requires_real_admin_jwt(seeded, path, principal):
    headers = {} if principal is None else seeded.headers(principal)
    if principal == 'invalid':
        headers = {'Authorization': 'Bearer invalid'}
    response = seeded.client.get(path, headers=headers)
    assert response.status_code == (200 if principal == 'admin' else 401), response.text


@pytest.mark.parametrize('principal', (None, 'invalid', 'pending'))
def test_object_route_rejects_unverified_identity(seeded, principal):
    seeded.run(seed_resource, seeded, 'note', True)
    headers = {} if principal is None else seeded.headers(principal)
    response = seeded.client.get('/api/v1/notes/target', headers=headers)
    assert response.status_code in (401, 403), response.text


@pytest.mark.parametrize('kind', ('chat', 'file', 'note', 'folder', 'model', 'prompt', 'tool', 'channel', 'knowledge'))
def test_admin_keeps_owned_resource_access_without_bypass(seeded, monkeypatch, kind):
    import importlib
    from open_webui import config
    from open_webui.models import chats
    from .test_matrix import request_for

    monkeypatch.setattr(config, 'BYPASS_ADMIN_ACCESS_CONTROL', False)
    monkeypatch.setattr(chats, 'ENABLE_ADMIN_CHAT_ACCESS', False)
    for name in ('files', 'notes', 'models', 'prompts', 'tools', 'knowledge'):
        monkeypatch.setattr(importlib.import_module(f'open_webui.routers.{name}'), 'BYPASS_ADMIN_ACCESS_CONTROL', False)

    async def prepare():
        model = await seed_resource(seeded, kind)
        async with seeded.sessions() as db:
            await db.execute(update(model).where(model.id == 'target').values(user_id='admin'))
            await db.commit()

    seeded.run(prepare)
    method, path, body = request_for(kind, 'read')
    response = seeded.client.request(method, path, headers=seeded.headers('admin'))
    assert response.status_code == 200, response.text
    assert response.json()['id'] == 'target'


def test_permissions_merge_groups_without_mutating_defaults(seeded):
    async def check():
        from open_webui.models.groups import Group, GroupMember
        from open_webui.utils.access_control import get_permissions, has_permission

        defaults = {'features': {'notes': False, 'channels': True}}
        original = deepcopy(defaults)
        async with seeded.sessions() as db:
            await db.execute(
                update(Group)
                .where(Group.id == 'readers')
                .values(permissions={'features': {'notes': True, 'channels': False}})
            )
            await db.execute(
                update(Group).where(Group.id == 'writers').values(permissions={'features': {'notes': False}})
            )
            db.add(GroupMember(id='second', group_id='writers', user_id='reader'))
            await db.commit()
            assert await get_permissions('reader', defaults, db=db) == {'features': {'notes': True, 'channels': True}}
            assert defaults == original
            assert await has_permission('reader', 'features.notes', deepcopy(defaults), db=db)
            assert await has_permission('outsider', 'features.channels', deepcopy(defaults), db=db)
            assert not await has_permission('outsider', 'features.notes', deepcopy(defaults), db=db)
            assert not await has_permission('reader', 'unknown.permission', deepcopy(defaults), db=db)

    seeded.run(check)


@pytest.mark.parametrize(
    'role,allow_users,allow_groups,allow_public,allow_anyone',
    [
        ('user', False, False, False, False),
        ('user', True, False, False, False),
        ('user', False, True, False, False),
        ('user', False, False, True, False),
        ('user', True, True, True, True),
        ('admin', False, False, False, False),
    ],
)
def test_filter_grants_by_seeded_sharing_permissions(
    seeded, role, allow_users, allow_groups, allow_public, allow_anyone
):
    async def check():
        from open_webui.models.groups import Group
        from open_webui.utils.access_control import filter_allowed_access_grants

        permissions = {
            'access_grants': {'allow_users': allow_users, 'allow_groups': allow_groups},
            'sharing': {'public_notes': allow_public, 'anyone': allow_anyone},
        }
        grants = [
            PUBLIC,
            {'principal_type': 'user', 'principal_id': 'outsider', 'permission': 'write'},
            GRANTS[0],
            {'principal_type': 'anyone', 'principal_id': '*', 'permission': 'read'},
        ]
        defaults = {
            'access_grants': {'allow_users': False, 'allow_groups': False},
            'sharing': {'public_notes': False, 'anyone': False},
        }
        async with seeded.sessions() as db:
            await db.execute(update(Group).where(Group.id == 'readers').values(permissions=permissions))
            await db.commit()
            result = await filter_allowed_access_grants(
                defaults, 'reader', role, grants, 'sharing.public_notes', 'sharing.anyone', db=db
            )
        expected = [
            grant
            for grant, allowed in zip(grants, (allow_public, allow_users, allow_groups, allow_anyone))
            if allowed or role == 'admin'
        ]
        assert result == expected

    seeded.run(check)


@pytest.mark.parametrize('kind', ('note', 'folder', 'model', 'prompt', 'tool', 'channel', 'knowledge'))
def test_database_grants_are_scoped_and_revocable(seeded, kind):
    async def check():
        from open_webui.models.access_grants import AccessGrants
        from open_webui.models.groups import Groups
        from open_webui.utils.access_control import has_access

        await seed_resource(seeded, kind)
        for principal, read, write in [
            ('admin', False, False),
            ('owner', False, False),
            ('reader', True, False),
            ('writer', True, True),
            ('outsider', False, False),
            ('other_group', False, False),
        ]:
            assert await AccessGrants.has_access(principal, kind, 'target', 'read') is read
            assert await AccessGrants.has_access(principal, kind, 'target', 'write') is write
        assert await AccessGrants.has_access('reader', kind, 'target', 'read')
        assert not await AccessGrants.has_access('reader', kind, 'target', 'write')
        assert not await AccessGrants.has_access('reader', kind, 'missing', 'read')
        assert not await AccessGrants.has_access('reader', 'unrelated_type', 'target', 'read')
        assert not await AccessGrants.has_access('other_group', kind, 'target', 'read')
        assert await AccessGrants.get_accessible_resource_ids('reader', kind, ['target', 'missing']) == {'target'}
        await Groups.set_group_user_ids_by_id('readers', [])
        assert not await AccessGrants.has_access('reader', kind, 'target', 'read')
        await AccessGrants.set_access_grants(
            kind, 'target', [{'principal_type': 'user', 'principal_id': 'outsider', 'permission': 'write'}]
        )
        assert await AccessGrants.has_access('outsider', kind, 'target', 'write')
        assert not await AccessGrants.has_access('outsider', kind, 'target', 'read')
        assert not await has_access('outsider', 'read', None)
        assert not await has_access('outsider', 'read', [])
        await AccessGrants.set_access_grants(kind, 'target', [])
        assert not await AccessGrants.has_access('outsider', kind, 'target', 'write')

    seeded.run(check)


def test_folder_inheritance_and_cycle_termination(seeded):
    async def check():
        from open_webui.models.folders import Folder, Folders
        from open_webui.utils.access_control.folders import has_folder_access, has_folder_write_access

        await seed_resource(seeded, 'folder')
        async with seeded.sessions() as db:
            db.add(Folder(id='child', parent_id='target', user_id='owner', name='child', created_at=1, updated_at=1))
            await db.commit()
            child = await Folders.get_folder_by_id('child', db=db)
            assert await has_folder_access('reader', child, 'read', db)
            assert not await has_folder_access('reader', child, 'write', db)
            assert await has_folder_write_access('writer', 'child', db=db)
            assert not await has_folder_write_access('writer', 'missing', db=db)
            await db.execute(update(Folder).where(Folder.id == 'target').values(parent_id='child'))
            await db.commit()
            assert not await has_folder_access('outsider', child, 'read', db)

    seeded.run(check)


@pytest.mark.parametrize('permission,allowed', [('read', True), ('write', False)])
def test_file_cannot_be_laundered_through_foreign_owned_knowledge(seeded, permission, allowed):
    async def check():
        from open_webui.models.knowledge import Knowledge
        from open_webui.models.users import Users
        from open_webui.utils.access_control.files import has_access_to_file

        await seed_resource(seeded, 'file')
        async with seeded.sessions() as db:
            await db.execute(update(Knowledge).where(Knowledge.id == 'file-kb').values(user_id='outsider'))
            await db.commit()
            user = await Users.get_user_by_id('outsider', db=db)
            assert await has_access_to_file('target', permission, user, db=db) is allowed
            assert not await has_access_to_file('missing', permission, user, db=db)

    seeded.run(check)


def test_folder_file_entries_require_valid_readable_targets(seeded):
    async def check():
        from open_webui.models.users import Users
        from open_webui.utils.access_control.files import get_accessible_folder_files, can_read_all_folder_files

        await seed_resource(seeded, 'note')
        user = await Users.get_user_by_id('reader')
        readable = {'type': 'note', 'id': 'target'}
        entries = [readable, {'type': 'note', 'id': 'missing'}, {'type': 'unknown', 'id': 'target'}, None]
        assert await get_accessible_folder_files(entries, user) == [readable]
        assert not await can_read_all_folder_files(entries, user)
        assert await can_read_all_folder_files([readable], user)
        assert await can_read_all_folder_files(None, user)
        assert not await can_read_all_folder_files({'files': entries}, user)

    seeded.run(check)


@pytest.mark.parametrize('bypass', (False, True))
@pytest.mark.parametrize('principal', PRINCIPALS)
def test_shared_chat_read_matrix(seeded, monkeypatch, bypass, principal):
    from open_webui.routers import chats

    # admin owner reader writer outsider other_group wildcard reader
    expected = ('AAAADDA' if bypass else 'DAAADDA')[PRINCIPALS.index(principal)] == 'A'
    monkeypatch.setattr(chats, 'ENABLE_ADMIN_CHAT_ACCESS', bypass)

    async def prepare():
        from open_webui.models.access_grants import AccessGrants
        from open_webui.models.chats import Chats
        from open_webui.models.shared_chats import SharedChats
        from open_webui.models.users import Users

        await seed_resource(seeded, 'chat')
        async with seeded.sessions() as db:
            shared = await SharedChats.create('target', 'owner', db=db)
            await Chats.update_chat_share_id_by_id('target', shared.id, db=db)
            await AccessGrants.set_access_grants(
                'shared_chat', 'target', [PUBLIC] if principal == 'public' else GRANTS, db=db
            )
            assert (
                await chats.can_read_shared_chat(await Users.get_user_by_id(principal, db=db), shared, db) is expected
            )
            return shared.id

    share_id = seeded.run(prepare)
    response = seeded.client.get(f'/api/v1/chats/share/{share_id}', headers=seeded.headers(principal))
    assert response.status_code == (200 if expected else 401), response.text


@pytest.mark.parametrize(
    'admin_bypass,model_bypass,allowed',
    [(False, False, False), (True, False, True), (False, True, True), (True, True, True)],
)
def test_model_listing_bypass_flags(seeded, monkeypatch, admin_bypass, model_bypass, allowed):
    from open_webui.utils import models

    monkeypatch.setattr(models, 'BYPASS_ADMIN_ACCESS_CONTROL', admin_bypass)
    monkeypatch.setattr(models, 'BYPASS_MODEL_ACCESS_CONTROL', model_bypass)
    monkeypatch.setattr(models, 'MODEL_WHITELIST', [])

    async def check():
        from open_webui.models.models import Models
        from open_webui.models.users import Users

        await seed_resource(seeded, 'model')
        model = await Models.get_model_by_id('target')
        result = await models.get_filtered_models(
            [{'id': 'target', 'info': model.model_dump()}], await Users.get_user_by_id('admin')
        )
        assert bool(result) is allowed

    seeded.run(check)
