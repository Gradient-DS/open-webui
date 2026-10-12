"""Chat sharing is off by default and gated by chat.share for admins and users alike."""

import pytest
from sqlalchemy.orm import Session

ANYONE = {'principal_type': 'anyone', 'principal_id': '*', 'permission': 'read'}
ALL_USERS = {'principal_type': 'user', 'principal_id': '*', 'permission': 'read'}
READERS = {'principal_type': 'group', 'principal_id': 'readers', 'permission': 'read'}


def set_sharing(seeded, share, public=False, open_=False):
    from open_webui.models.config import Config

    async def apply():
        permissions = await Config.get('user.permissions')
        permissions['chat']['share'] = share
        permissions['sharing']['public_chats'] = public
        permissions['sharing']['open_chats'] = open_
        await Config.upsert({'user.permissions': permissions})

    seeded.run(apply)


def seed_chat(seeded, owner, shared=False, grants=()):
    from open_webui.models.access_grants import AccessGrants
    from open_webui.models.chats import Chat, Chats
    from open_webui.models.shared_chats import SharedChats

    async def apply():
        async with seeded.sessions() as db:
            db.add(
                Chat(id='target', user_id=owner, title='t', chat={'title': 't'}, meta={}, created_at=1, updated_at=1)
            )
            await db.commit()
            if shared:
                record = await SharedChats.create('target', owner, db=db)
                await Chats.update_chat_share_id_by_id('target', record.id, db=db)
                await AccessGrants.set_access_grants('shared_chat', 'target', list(grants), db=db)
                return record.id

    return seeded.run(apply)


def share_id(seeded):
    from open_webui.models.chats import Chat

    with Session(seeded.engine) as db:
        return db.get(Chat, 'target').share_id


def grants(seeded):
    from open_webui.models.access_grants import AccessGrants

    rows = seeded.run(AccessGrants.get_grants_by_resource, 'shared_chat', 'target')
    return {(g.principal_type, g.principal_id, g.permission) for g in rows}


def test_chat_share_defaults_off():
    from open_webui.config import DEFAULT_USER_PERMISSIONS

    assert DEFAULT_USER_PERMISSIONS['chat']['share'] is False
    assert DEFAULT_USER_PERMISSIONS['sharing']['public_chats'] is False
    assert DEFAULT_USER_PERMISSIONS['sharing']['open_chats'] is False


@pytest.mark.parametrize('principal', ('admin', 'owner'))
def test_share_refused_without_permission(seeded, principal):
    set_sharing(seeded, share=False)
    seed_chat(seeded, principal)
    response = seeded.client.post('/api/v1/chats/target/share', headers=seeded.headers(principal))
    assert response.status_code == 401, response.text
    assert share_id(seeded) is None


@pytest.mark.parametrize('principal', ('admin', 'owner'))
def test_share_resnapshot_refused_without_permission(seeded, principal):
    set_sharing(seeded, share=False)
    existing = seed_chat(seeded, principal, shared=True)
    response = seeded.client.post('/api/v1/chats/target/share', headers=seeded.headers(principal))
    assert response.status_code == 401, response.text
    assert share_id(seeded) == existing


@pytest.mark.parametrize('principal', ('admin', 'owner'))
def test_share_allowed_when_enabled(seeded, principal):
    set_sharing(seeded, share=True)
    seed_chat(seeded, principal)
    response = seeded.client.post('/api/v1/chats/target/share', headers=seeded.headers(principal))
    assert response.status_code == 200, response.text
    assert share_id(seeded)


@pytest.mark.parametrize('principal', ('admin', 'owner'))
def test_adding_grants_refused_without_permission(seeded, principal):
    set_sharing(seeded, share=False, public=True, open_=True)
    seed_chat(seeded, principal, shared=True)
    response = seeded.client.post(
        '/api/v1/chats/shared/target/access/update',
        json={'access_grants': [READERS]},
        headers=seeded.headers(principal),
    )
    assert response.status_code == 401, response.text
    assert grants(seeded) == set()


@pytest.mark.parametrize('principal', ('admin', 'owner'))
def test_revoking_grants_allowed_without_permission(seeded, principal):
    set_sharing(seeded, share=False)
    seed_chat(seeded, principal, shared=True, grants=[READERS, ANYONE])
    response = seeded.client.post(
        '/api/v1/chats/shared/target/access/update',
        json={'access_grants': [READERS]},
        headers=seeded.headers(principal),
    )
    assert response.status_code == 200, response.text
    assert grants(seeded) == {('group', 'readers', 'read')}


@pytest.mark.parametrize('principal', ('admin', 'owner'))
def test_open_and_public_grants_need_their_permissions(seeded, principal):
    set_sharing(seeded, share=True, public=False, open_=False)
    seed_chat(seeded, principal, shared=True)
    response = seeded.client.post(
        '/api/v1/chats/shared/target/access/update',
        json={'access_grants': [ANYONE, ALL_USERS, READERS]},
        headers=seeded.headers(principal),
    )
    assert response.status_code == 200, response.text
    assert grants(seeded) == {('group', 'readers', 'read')}


@pytest.mark.parametrize('principal', ('admin', 'owner'))
def test_open_and_public_grants_allowed_when_enabled(seeded, principal):
    set_sharing(seeded, share=True, public=True, open_=True)
    seed_chat(seeded, principal, shared=True)
    response = seeded.client.post(
        '/api/v1/chats/shared/target/access/update',
        json={'access_grants': [ANYONE, ALL_USERS]},
        headers=seeded.headers(principal),
    )
    assert response.status_code == 200, response.text
    assert grants(seeded) == {('anyone', '*', 'read'), ('user', '*', 'read')}


def test_existing_open_link_stays_readable_when_sharing_off(seeded):
    set_sharing(seeded, share=False)
    existing = seed_chat(seeded, 'owner', shared=True, grants=[ANYONE])
    response = seeded.client.get(f'/api/v1/chats/share/{existing}')
    assert response.status_code == 200, response.text
