"""A share grant reaches the snapshot taken at share time, never the live chat, and dies with the chat."""

import pytest
from sqlalchemy import update

from .test_matrix import GRANTS


def message(message_id: str, parent: str | None, content: str, files: list[str]) -> dict:
    return {
        'id': message_id,
        'parentId': parent,
        'childrenIds': [],
        'role': 'user',
        'content': content,
        'files': [{'type': 'file', 'id': file_id} for file_id in files],
    }


def history(*messages: dict) -> dict:
    return {'title': 't', 'history': {'messages': {m['id']: m for m in messages}, 'currentId': messages[-1]['id']}}


async def link(db, file_id: str, message_id: str) -> None:
    from open_webui.models.chats import ChatFile
    from open_webui.models.files import File

    db.add(
        File(
            id=file_id,
            user_id='owner',
            filename=f'{file_id}.txt',
            path='',
            meta={},
            data={},
            created_at=1,
            updated_at=1,
        )
    )
    db.add(
        ChatFile(
            id=f'cf-{file_id}',
            user_id='owner',
            chat_id='target',
            message_id=message_id,
            file_id=file_id,
            created_at=1,
            updated_at=1,
        )
    )
    await db.commit()


def seed(seeded) -> str:
    """Share a chat holding `early`, then continue it with a secret message and two later files."""
    from open_webui.models.access_grants import AccessGrants
    from open_webui.models.chats import Chat, Chats
    from open_webui.models.shared_chats import SharedChats

    first = message('u1', None, 'Shared question', ['early'])

    async def apply():
        async with seeded.sessions() as db:
            db.add(
                Chat(id='target', user_id='owner', title='t', chat=history(first), meta={}, created_at=1, updated_at=1)
            )
            await db.commit()
            await link(db, 'early', 'u1')
            record = await SharedChats.create('target', 'owner', db=db)
            await Chats.update_chat_share_id_by_id('target', record.id, db=db)
            await AccessGrants.set_access_grants('shared_chat', 'target', GRANTS, db=db)

            later = message('u2', 'u1', 'Secret follow-up', ['late'])
            await db.execute(
                update(Chat).where(Chat.id == 'target').values(chat=history({**first, 'childrenIds': ['u2']}, later))
            )
            await db.commit()
            await link(db, 'late', 'u2')
            # A file linked later to a message the snapshot does hold is not part of it either.
            await link(db, 'late-same-message', 'u1')
            return record.id

    return seeded.run(apply)


def test_a_grantee_reads_the_snapshot_but_not_the_live_chat(seeded):
    share_id = seed(seeded)
    reader = seeded.headers('reader')

    shared = seeded.client.get(f'/api/v1/chats/share/{share_id}', headers=reader)
    assert shared.status_code == 200, shared.text
    assert 'u2' not in shared.json()['chat']['history']['messages']

    for path in ('/api/v1/chats/target', '/api/v1/chats/target/tags'):
        response = seeded.client.get(path, headers=reader)
        assert response.status_code == 401, (path, response.text)
    owner = seeded.client.get('/api/v1/chats/target', headers=seeded.headers('owner'))
    assert 'u2' in owner.json()['chat']['history']['messages']


@pytest.mark.parametrize('file_id,status', [('early', 200), ('late', 404), ('late-same-message', 404)])
def test_a_grantee_reads_only_the_files_in_the_snapshot(seeded, file_id, status):
    seed(seeded)

    response = seeded.client.get(f'/api/v1/files/{file_id}', headers=seeded.headers('reader'))
    assert response.status_code == status, response.text
    assert seeded.client.get(f'/api/v1/files/{file_id}', headers=seeded.headers('outsider')).status_code == 404


def test_resharing_brings_later_files_into_the_snapshot(seeded):
    seed(seeded)

    assert seeded.client.post('/api/v1/chats/target/share', headers=seeded.headers('owner')).status_code == 200
    response = seeded.client.get('/api/v1/files/late', headers=seeded.headers('reader'))
    assert response.status_code == 200, response.text


def test_deleting_the_chat_revokes_its_share(seeded):
    from open_webui.models.access_grants import AccessGrants
    from open_webui.models.shared_chats import SharedChats

    share_id = seed(seeded)
    reader = seeded.headers('reader')

    assert seeded.client.delete('/api/v1/chats/target', headers=seeded.headers('owner')).json() is True

    assert seeded.client.get(f'/api/v1/chats/share/{share_id}', headers=reader).status_code == 401
    assert seeded.client.post(f'/api/v1/chats/{share_id}/clone/shared', headers=reader).status_code == 401
    assert seeded.client.get('/api/v1/files/early', headers=reader).status_code == 404
    assert seeded.run(SharedChats.get_by_chat_id, 'target') is None
    assert seeded.run(AccessGrants.get_grants_by_resource, 'shared_chat', 'target') == []


def test_a_share_of_an_already_deleted_chat_is_refused(seeded):
    """Rows left by deletes from before shares were revoked on delete stay unreadable."""
    from open_webui.models.chats import Chat

    share_id = seed(seeded)

    async def soft_delete_only():
        async with seeded.sessions() as db:
            await db.execute(update(Chat).where(Chat.id == 'target').values(deleted_at=2))
            await db.commit()

    seeded.run(soft_delete_only)
    reader = seeded.headers('reader')
    assert seeded.client.get(f'/api/v1/chats/share/{share_id}', headers=reader).status_code == 401
    assert seeded.client.post(f'/api/v1/chats/{share_id}/clone/shared', headers=reader).status_code == 401


@pytest.mark.parametrize('scope', ('user', 'folder'))
def test_bulk_soft_deletes_revoke_shares(seeded, scope):
    from open_webui.models.access_grants import AccessGrants
    from open_webui.models.chats import Chat, Chats
    from open_webui.models.shared_chats import SharedChats

    seed(seeded)

    async def delete():
        if scope == 'user':
            return await Chats.soft_delete_by_user_id('owner')
        async with seeded.sessions() as db:
            await db.execute(update(Chat).where(Chat.id == 'target').values(folder_id='f1'))
            await db.commit()
        return await Chats.soft_delete_by_user_id_and_folder_id('owner', 'f1')

    assert seeded.run(delete) == 1
    assert seeded.run(SharedChats.get_by_chat_id, 'target') is None
    assert seeded.run(AccessGrants.get_grants_by_resource, 'shared_chat', 'target') == []
