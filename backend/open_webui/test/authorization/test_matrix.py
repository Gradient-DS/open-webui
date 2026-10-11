"""Owner, grant, and administrator policy against real rows and real JWTs."""

import importlib

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

# Columns: admin, owner, reader, writer (read + write), outsider, other_group, public.
# A = allow, D = deny; public uses a separate wildcard-read copy of the object.
# Admin columns are explicit for bypass off/on. Chat read uses ENABLE_ADMIN_CHAT_ACCESS.
# Grants are exact permissions, not an implicit write-implies-read hierarchy.
PRINCIPALS = ('admin', 'owner', 'reader', 'writer', 'outsider', 'other_group', 'public')
MATRIX = [
    # object     action    bypass off  bypass on
    ('chat', 'read', 'DADDDDD', 'AADDDDD'),
    ('chat', 'update', 'DADDDDD', 'DADDDDD'),
    ('chat', 'delete', 'DADDDDD', 'AADDDDD'),
    ('chat', 'share', 'DADDDDD', 'DADDDDD'),  # Native chats are owner-only; snapshots have grants.
    ('file', 'read', 'DAAADDA', 'AAAADDA'),
    ('file', 'update', 'DADADDD', 'AADADDD'),
    ('file', 'delete', 'DADADDD', 'AADADDD'),
    ('file', 'share', None, None),  # File access is delegated through a knowledge base/model.
    ('note', 'read', 'DAAADDA', 'AAAADDA'),
    ('note', 'update', 'DADADDD', 'AADADDD'),
    ('note', 'delete', 'DADADDD', 'AADADDD'),
    ('note', 'share', 'DADADDD', 'AADADDD'),
    ('folder', 'read', 'DAAADDA', 'AAAADDA'),
    ('folder', 'update', 'DADADDD', 'AADADDD'),
    ('folder', 'delete', 'DADDDDD', 'AADDDDD'),  # Cascading deletion is reserved for owner/admin.
    ('folder', 'share', 'DADADDD', 'AADADDD'),
    ('model', 'read', 'DAAADDA', 'AAAADDA'),
    ('model', 'update', 'DADADDD', 'AADADDD'),
    ('model', 'delete', 'DADADDD', 'AADADDD'),
    ('model', 'share', 'DADADDD', 'AADADDD'),
    ('prompt', 'read', 'DAAADDA', 'AAAADDA'),
    ('prompt', 'update', 'DADADDD', 'AADADDD'),
    ('prompt', 'delete', 'DADADDD', 'AADADDD'),
    ('prompt', 'share', 'DADADDD', 'AADADDD'),
    ('tool', 'read', 'DAAADDA', 'AAAADDA'),
    ('tool', 'update', 'DADADDD', 'AADADDD'),
    ('tool', 'delete', 'DADADDD', 'AADADDD'),
    ('tool', 'share', 'DADADDD', 'AADADDD'),
    ('channel', 'read', 'DAAADDA', 'AAAADDA'),
    ('channel', 'update', 'DADDDDD', 'AADDDDD'),  # Write grants permit messages, not channel administration.
    ('channel', 'delete', 'DADDDDD', 'AADDDDD'),
    ('channel', 'share', 'DADDDDD', 'AADDDDD'),
    ('knowledge', 'read', 'DAAADDA', 'AAAADDA'),
    ('knowledge', 'update', 'DADADDD', 'AADADDD'),
    ('knowledge', 'delete', 'DADADDD', 'AADADDD'),
    ('knowledge', 'share', 'DADADDD', 'AADADDD'),
    ('skill', 'read', 'DAAADDA', 'AAAADDA'),
    ('skill', 'update', 'DADADDD', 'AADADDD'),
    ('skill', 'delete', 'DADADDD', 'AADADDD'),
    ('skill', 'share', 'DADADDD', 'AADADDD'),
]

# Low-level helpers deliberately do not confer ownership/admin privileges unless documented.
FUNCTION_MATRIX = [
    # helper                       read       write
    ('has_access', 'DDAADDA', 'DDDADDD'),
    ('AccessGrants.has_access', 'DDAADDA', 'DDDADDD'),
    ('has_access_to_file', 'DAAADDA', 'DADADDD'),
    ('has_folder_access', 'DAAADDA', 'DADADDD'),
    ('channel_has_access', 'DDAADDA', 'DDDADDD'),
]

GRANTS = [
    {'principal_type': 'group', 'principal_id': 'readers', 'permission': 'read'},
    {'principal_type': 'group', 'principal_id': 'writers', 'permission': 'read'},
    {'principal_type': 'group', 'principal_id': 'writers', 'permission': 'write'},
]
PUBLIC = {'principal_type': 'user', 'principal_id': '*', 'permission': 'read'}
TOOL_CODE = 'class Tools:\n    pass\n'


async def seed_resource(seeded, kind, public=False):
    from open_webui.models.access_grants import AccessGrants
    from open_webui.models.knowledge import Knowledge

    names = {
        'chat': ('chats', 'Chat', {'title': 'original', 'chat': {'title': 'original'}, 'meta': {}}),
        'file': ('files', 'File', {'filename': 'original.txt', 'path': '', 'meta': {}, 'data': {}}),
        'note': ('notes', 'Note', {'title': 'original', 'data': {'content': {'md': 'original'}}}),
        'folder': ('folders', 'Folder', {'name': 'original', 'data': {}}),
        'model': ('models', 'Model', {'name': 'original', 'params': {}, 'meta': {}}),
        'prompt': ('prompts', 'Prompt', {'name': 'original', 'command': '/original', 'content': 'original'}),
        'tool': ('tools', 'Tool', {'name': 'original', 'content': TOOL_CODE, 'specs': [], 'meta': {}}),
        'skill': (
            'skills',
            'Skill',
            {'name': 'original', 'content': 'original', 'description': 'original', 'meta': {}},
        ),
        'channel': ('channels', 'Channel', {'name': 'original'}),
        'knowledge': ('knowledge', 'Knowledge', {'name': 'original', 'description': 'original'}),
    }
    module, name, fields = names[kind]
    model = getattr(importlib.import_module(f'open_webui.models.{module}'), name)
    async with seeded.sessions() as db:
        db.add(model(id='target', user_id='owner', created_at=1, updated_at=1, **fields))
        if kind == 'file':
            db.add(Knowledge(id='file-kb', user_id='owner', name='file-kb', description='', created_at=1, updated_at=1))
        await db.commit()
        if kind == 'file':
            from open_webui.models.files import Files

            await Files.update_file_metadata_by_id('target', {'collection_name': 'file-kb'}, db=db)
        if kind != 'chat':
            await AccessGrants.set_access_grants(
                'knowledge' if kind == 'file' else kind,
                'file-kb' if kind == 'file' else 'target',
                [PUBLIC] if public else GRANTS,
                db=db,
            )
    return model


def request_for(kind, action):
    plural = {
        'knowledge': 'knowledge',
        'chat': 'chats',
        'file': 'files',
        'note': 'notes',
        'folder': 'folders',
        'model': 'models',
        'prompt': 'prompts',
        'tool': 'tools',
        'skill': 'skills',
        'channel': 'channels',
    }[kind]
    path = f'/api/v1/{plural}'
    if kind == 'model':
        return {
            'read': ('GET', path + '/model?id=target', None),
            'update': ('POST', path + '/model/update', {'id': 'target', 'name': 'changed', 'meta': {}, 'params': {}}),
            'delete': ('POST', path + '/model/delete', {'id': 'target'}),
            'share': ('POST', path + '/model/access/update', {'id': 'target', 'access_grants': []}),
        }[action]
    path += ('/id' if kind in ('prompt', 'tool', 'skill') else '') + '/target'
    body = {
        'chat': {'chat': {'title': 'changed'}},
        'file': {'filename': 'changed.txt'},
        'note': {'title': 'changed'},
        'folder': {'name': 'changed'},
        'prompt': {'command': '/original', 'name': 'changed', 'content': 'changed'},
        'tool': {'id': 'target', 'name': 'changed', 'content': TOOL_CODE, 'meta': {}},
        'skill': {'id': 'target', 'name': 'changed', 'description': 'changed', 'content': 'changed', 'meta': {}},
        'channel': {'name': 'changed'},
        'knowledge': {'name': 'changed', 'description': 'changed'},
    }[kind]
    if action == 'read':
        return 'GET', path, None
    if action == 'update':
        return 'POST', path + {'chat': '', 'file': '/rename', 'folder': '/update'}.get(kind, '/update'), body
    if action == 'delete':
        return 'DELETE', path + ('' if kind in ('chat', 'file', 'folder') else '/delete'), None
    if kind == 'chat':
        return 'POST', path + '/share', None
    return 'POST', path + ('/update' if kind == 'channel' else '/access/update'), {'access_grants': []}


CASES = [
    pytest.param(
        kind, action, principal, bypass, cells[index] == 'A', id=f'{kind}-{action}-{principal}-bypass={bypass}'
    )
    for kind, action, off, on in MATRIX
    if off is not None
    for bypass, cells in ((False, off), (True, on))
    for index, principal in enumerate(PRINCIPALS)
]


@pytest.mark.parametrize('kind,action,principal,bypass,allowed', CASES)
def test_http_matrix(seeded, monkeypatch, kind, action, principal, bypass, allowed):
    from open_webui import config

    monkeypatch.setattr(config, 'BYPASS_ADMIN_ACCESS_CONTROL', bypass)
    monkeypatch.setattr(config, 'ENABLE_ADMIN_CHAT_ACCESS', bypass)
    monkeypatch.setattr(config, 'ENABLE_ADMIN_EXPORT', bypass)
    model = seeded.run(seed_resource, seeded, kind, principal == 'public')
    from open_webui.models.access_grants import AccessGrants

    before_grants = seeded.run(AccessGrants.get_grants_by_resource, kind, 'target')
    with Session(seeded.engine) as db:
        before = dict(db.execute(select(model.__table__)).mappings().one())
    method, path, body = request_for(kind, action)
    response = seeded.client.request(method, path, json=body, headers=seeded.headers(principal))
    if allowed:
        assert response.status_code == 200, response.text
        assert response.json() is not None and response.json() is not False
        if action == 'read':
            assert response.json()['id'] == 'target'
        with Session(seeded.engine) as db:
            after = db.get(model, 'target')
            if action == 'update':
                field = {'chat': 'title', 'note': 'title', 'file': 'filename'}.get(kind, 'name')
                assert getattr(after, field) == ('changed.txt' if kind == 'file' else 'changed')
            elif action == 'delete':
                assert after is None or after.deleted_at is not None
            elif action == 'share' and kind == 'chat':
                assert after.share_id
            elif action == 'share':
                assert seeded.run(AccessGrants.get_grants_by_resource, kind, 'target') == []
    else:
        assert response.status_code in (400, 401, 403, 404), response.text
        with Session(seeded.engine) as db:
            assert dict(db.execute(select(model.__table__)).mappings().one()) == before
        assert seeded.run(AccessGrants.get_grants_by_resource, kind, 'target') == before_grants


@pytest.mark.parametrize('helper,read,write', FUNCTION_MATRIX)
@pytest.mark.parametrize('principal', PRINCIPALS)
@pytest.mark.parametrize('permission', ('read', 'write'))
def test_function_matrix(seeded, helper, read, write, principal, permission):
    async def check():
        from open_webui.models.access_grants import AccessGrants
        from open_webui.models.users import Users
        from open_webui.utils.access_control import has_access
        from open_webui.utils.access_control.files import has_access_to_file
        from open_webui.utils.access_control.folders import has_folder_access

        kind = {'has_access_to_file': 'file', 'has_folder_access': 'folder', 'channel_has_access': 'channel'}.get(
            helper, 'note'
        )
        await seed_resource(seeded, kind, principal == 'public')
        async with seeded.sessions() as db:
            if helper == 'has_access':
                return await has_access(principal, permission, [PUBLIC] if principal == 'public' else GRANTS, db=db)
            if helper == 'AccessGrants.has_access':
                return await AccessGrants.has_access(principal, kind, 'target', permission, db=db)
            if helper == 'has_access_to_file':
                return await has_access_to_file(
                    'target', permission, await Users.get_user_by_id(principal, db=db), db=db
                )
            if helper == 'has_folder_access':
                from open_webui.models.folders import Folders

                return await has_folder_access(
                    principal, await Folders.get_folder_by_id('target', db=db), permission, db
                )
            from open_webui.models.channels import Channels
            from open_webui.routers.channels import channel_has_access

            return await channel_has_access(
                principal, await Channels.get_channel_by_id('target', db=db), permission, db=db
            )

    assert seeded.run(check) is ((read if permission == 'read' else write)[PRINCIPALS.index(principal)] == 'A')
