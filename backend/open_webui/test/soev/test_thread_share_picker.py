"""The share picker uses bound people and the existing directory's group scope."""

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from open_webui.internal.db import get_async_session
from open_webui.models.folders import Folder
from open_webui.models.groups import Group
from open_webui.models.users import User
from open_webui.routers import users
from open_webui.soev.thread_sharing import named_shares
from open_webui.soev.threads import Share
from open_webui.utils.auth import get_verified_user

PERSON = 'platform:user:70c51d1b-8310-4649-89bd-084764d104f6'
GROUP = '13b95f20-57d8-48c6-9a13-042bcf78d021'
HIDDEN = '624ece59-0d53-46f0-9e2c-a6427ab2e521'


@pytest.mark.asyncio
async def test_person_search_filters_before_paging_and_exposes_only_bindings(group_db):
    for index in range(31):
        group_db.add(
            User(
                id=f'unbound-{index}',
                name=f'A {index}',
                email=f'a-{index}@example.invalid',
                role='user',
                last_active_at=1,
                created_at=1,
                updated_at=1,
            )
        )
    await group_db.commit()
    app = FastAPI()
    app.include_router(users.router, prefix='/users')

    async def session():
        yield group_db

    app.dependency_overrides[get_async_session] = session
    app.dependency_overrides[get_verified_user] = lambda: SimpleNamespace(id='fern', role='user')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owui.invalid') as client:
        response = await client.get('/users/search?platform_people=true&order_by=name&direction=asc')
        assert response.status_code == 200, response.text
        data = response.json()
        assert data['total'] == 2
        assert [person['id'] for person in data['users']] == ['fern', 'lark']
        assert data['users'][1]['person_id'] == PERSON
        assert data['users'][1]['groups'] == []
        assert not any('oauth' in person or 'settings' in person for person in data['users'])
        result = await client.get('/users/search?platform_people=true&query=Lark')
        assert [person['person_id'] for person in result.json()['users']] == [PERSON]
        assert (await client.get('/users/search?platform_people=true&page=2')).json()['users'] == []
        regular = (await client.get('/users/search?query=Lark')).json()
        assert regular['users'][0]['person_id'] is None
        assert (await client.get('/users/search')).json()['total'] == 34


def share(kind, id):
    return Share.model_validate({'audience': {'kind': kind, 'id': id}, 'at': 7})


@pytest.mark.asyncio
@pytest.mark.parametrize('role', ['user', 'admin'])
async def test_share_names_preserve_group_visibility_and_unknown_audiences(group_db, role):
    group_db.add(Group(id=HIDDEN, user_id='', name='Hidden group', description='', created_at=1, updated_at=1))
    await group_db.commit()
    shares = [
        share('person', PERSON),
        share('group', f'platform:group:{GROUP}'),
        share('group', f'platform:group:{HIDDEN}'),
        share('person', 'missing-person'),
    ]
    result = await named_shares(shares, SimpleNamespace(id='fern', role=role), db=group_db)
    assert result[0]['name'] == 'Lark'
    assert result[1]['name'] == 'Old'
    assert result[2].get('name') == ('Hidden group' if role == 'admin' else None)
    assert 'name' not in result[3]
    assert [item['audience'] for item in result] == [item.audience.model_dump() for item in shares]


@pytest.mark.asyncio
async def test_folder_share_names_require_read_access(group_db):
    connection = await group_db.connection()
    await connection.run_sync(lambda db: Folder.__table__.create(db))
    group_db.add_all(
        [
            Folder(id='own-folder', user_id='fern', name='My folder', created_at=1, updated_at=1),
            Folder(id='hidden-folder', user_id='lark', name='Private folder', created_at=1, updated_at=1),
        ]
    )
    await group_db.commit()
    result = await named_shares(
        [share('folder', 'own-folder'), share('folder', 'hidden-folder'), share('folder', 'gone')],
        SimpleNamespace(id='fern', role='user'),
        db=group_db,
    )
    assert result[0]['name'] == 'My folder'
    assert 'name' not in result[1]
    assert 'name' not in result[2]
