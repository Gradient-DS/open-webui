"""SCIM attribute spelling cannot bypass validation or change attribute values."""

from unittest.mock import AsyncMock

import pytest
from open_webui.test.soev.test_admin_person_boundary import admin_app as admin_app
from open_webui.test.soev.test_keycloak_token import token_app as token_app


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'method,resource,body',
    [
        ('PUT', 'Users/elm', {'active': True, 'Active': False, 'displayName': 'Cedar'}),
        ('PUT', 'Users/elm', {'Active': False, 'active': True, 'externalId': 'cedar'}),
        ('POST', 'Users', {'active': True, 'ACTIVE': False}),
        ('POST', 'Users', {'Name': {'givenName': 'Cedar', 'GIVENNAME': 'Willow'}}),
        ('PUT', 'Users/elm', {'Emails': [{'value': 'cedar@example.invalid', 'VALUE': 'willow@example.invalid'}]}),
        ('PUT', 'Users/elm', {'Photos': [{'value': '/cedar.png', 'VALUE': '/willow.png'}]}),
        ('PATCH', 'Users/elm', {'Operations': [], 'OPERATIONS': [{'op': 'replace', 'path': 'active', 'value': False}]}),
        ('PATCH', 'Users/elm', {'Operations': [{'op': 'replace', 'OP': 'add', 'path': 'active', 'value': True}]}),
        ('PATCH', 'Users/elm', {'Operations': [{'op': 'replace', 'value': {'active': True, 'ACTIVE': False}}]}),
        ('POST', 'Groups', {'displayName': 'Cedar', 'DISPLAYNAME': 'Willow'}),
        ('PUT', 'Groups/cedar', {'members': [], 'MEMBERS': [{'value': 'elm'}]}),
        ('PUT', 'Groups/cedar', {'members': [{'value': 'elm', 'VALUE': 'willow'}]}),
        ('PATCH', 'Groups/cedar', {'Operations': [{'op': 'replace', 'path': 'displayName', 'PATH': 'members'}]}),
    ],
)
async def test_conflicting_case_variants_refuse_before_effects(admin_app, monkeypatch, method, resource, body):
    from open_webui.routers import scim

    writes = []
    for model, methods in (
        (scim.Users, ('insert_new_user', 'update_user_by_id', 'update_user_scim_by_id')),
        (scim.Groups, ('insert_new_group', 'update_group_by_id', 'set_group_user_ids_by_id')),
    ):
        for name in methods:
            write = AsyncMock()
            monkeypatch.setattr(model, name, write)
            writes.append(write)
    publish = AsyncMock()
    monkeypatch.setattr(scim, 'publish_event', publish)

    response = await admin_app.ctx.browser.request(method, f'/scim/v2/{resource}', json=body)

    assert response.status_code == 400, response.text
    for write in writes:
        write.assert_not_awaited()
    publish.assert_not_awaited()
    assert admin_app.ctx.requests == []
    admin_app.resolve.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('method', ['POST', 'PUT'])
async def test_user_resources_accept_mixed_case_and_nested_attributes(admin_app, method):
    body = {
        'SCHEMAS': ['urn:ietf:params:scim:schemas:core:2.0:User'],
        'USERNAME': 'cedar@example.invalid',
        'DISPLAYNAME': 'Cedar Vale',
        'NAME': {'GIVENNAME': 'Cedar', 'FAMILYNAME': 'Vale'},
        'EMAILS': [{'VALUE': 'cedar@example.invalid', 'TYPE': 'work', 'PRIMARY': True}],
        'PHOTOS': [{'VALUE': 'https://images.example.invalid/Cedar.png'}],
        'EXTERNALID': 'Cedar-External',
        'ACTIVE': False if method == 'POST' else True,
    }
    path = '/scim/v2/Users' if method == 'POST' else '/scim/v2/Users/elm'

    response = await admin_app.ctx.browser.request(method, path, json=body)

    assert response.status_code == (201 if method == 'POST' else 200), response.text
    resource = response.json()
    target = await admin_app.db.get(admin_app.user_table, resource['id'])
    await admin_app.db.refresh(target)
    assert (target.name, target.email, target.profile_image_url) == (
        'Cedar Vale',
        'cedar@example.invalid',
        'https://images.example.invalid/Cedar.png',
    )
    assert target.role == ('pending' if method == 'POST' else 'user')
    assert target.scim == {'fictional-provider': {'external_id': 'Cedar-External'}}
    assert resource['active'] is (method != 'POST')
    assert admin_app.ctx.requests == []
    admin_app.resolve.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('role', ['user', 'admin'])
async def test_mixed_case_patch_envelope_cannot_hide_suspension(admin_app, monkeypatch, role):
    from open_webui.routers import scim

    target = await admin_app.db.get(admin_app.user_table, 'elm')
    target.role = role
    await admin_app.db.commit()
    update = AsyncMock(wraps=scim.Users.update_user_by_id)
    stamp = AsyncMock(wraps=scim.Users.update_user_scim_by_id)
    publish = AsyncMock()
    monkeypatch.setattr(scim.Users, 'update_user_by_id', update)
    monkeypatch.setattr(scim.Users, 'update_user_scim_by_id', stamp)
    monkeypatch.setattr(scim, 'publish_event', publish)

    response = await admin_app.ctx.browser.patch(
        '/scim/v2/Users/elm',
        json={
            'OPERATIONS': [{'OP': 'replace', 'VALUE': {'DISPLAYNAME': 'Cedar', 'EXTERNALID': 'Cedar', 'ACTIVE': False}}]
        },
    )

    assert response.status_code == 403, response.text
    assert response.json()['detail']['constraint'] == 'suspension:console'
    await admin_app.db.refresh(target)
    assert (target.role, target.name, target.scim) == (role, 'Elm', None)
    update.assert_not_awaited()
    stamp.assert_not_awaited()
    publish.assert_not_awaited()
    assert admin_app.ctx.requests == []
    admin_app.resolve.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('method', ['POST', 'PUT', 'PATCH'])
async def test_group_resources_refuse_mixed_case_mutations(admin_app, monkeypatch, method):
    from open_webui.routers import scim

    writes = []
    for name in ('insert_new_group', 'update_group_by_id', 'set_group_user_ids_by_id'):
        write = AsyncMock()
        monkeypatch.setattr(scim.Groups, name, write)
        writes.append(write)
    publish = AsyncMock()
    monkeypatch.setattr(scim, 'publish_event', publish)
    path = '/scim/v2/Groups' if method == 'POST' else '/scim/v2/Groups/cedar'
    body = {'DISPLAYNAME': 'Cedar', 'MEMBERS': [{'VALUE': 'elm', 'DISPLAY': 'Elm', '$REF': '/Users/elm'}]}
    if method == 'PATCH':
        body = {'OPERATIONS': [{'OP': 'replace', 'PATH': path, 'VALUE': value} for path, value in body.items()]}

    response = await admin_app.ctx.browser.request(method, path, json=body)

    assert response.status_code == 403, response.text
    assert response.json()['detail']['code'] == 'platform_groups_read_only'
    for write in writes:
        write.assert_not_awaited()
    publish.assert_not_awaited()
    assert admin_app.ctx.requests == []
    admin_app.resolve.assert_not_awaited()


def test_group_patch_path_normalization_preserves_case_in_quoted_member_ids():
    from open_webui.routers.scim import normalize_scim_path

    assert normalize_scim_path('MEMBERS[VALUE eq "DISPLAYNAME"]') == 'members[value eq "DISPLAYNAME"]'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'resource,attribute', [('Users', 'USERNAME'), ('Users', 'EXTERNALID'), ('Groups', 'DISPLAYNAME')]
)
async def test_list_filters_normalize_attributes_and_preserve_values(admin_app, monkeypatch, resource, attribute):
    from open_webui.routers import scim

    users = AsyncMock(return_value={'users': [], 'total': 0})
    external = AsyncMock(return_value=None)
    groups = AsyncMock(return_value=None)
    monkeypatch.setattr(scim.Users, 'get_scim_users', users)
    monkeypatch.setattr(scim, 'find_user_by_external_id', external)
    monkeypatch.setattr(scim.Groups, 'get_group_by_name', groups)

    response = await admin_app.ctx.browser.get(
        f'/scim/v2/{resource}', params={'filter': f'{attribute} eq "DISPLAYNAME"'}
    )

    assert response.status_code == 200, response.text
    if attribute == 'USERNAME':
        users.assert_awaited_once_with(filter={'email': 'DISPLAYNAME'}, limit=1, db=admin_app.db)
    elif attribute == 'EXTERNALID':
        external.assert_awaited_once_with('DISPLAYNAME', db=admin_app.db)
    else:
        groups.assert_awaited_once_with('DISPLAYNAME', db=admin_app.db)
