"""A clone of another user's shared chat keeps the conversation but none of the owner's agent threads."""

import pytest

from .test_matrix import GRANTS

BOOKMARK = {'thread_id': 'thr-owner', 'position': 7}


def conversation() -> dict:
    messages = {
        'u1': {'id': 'u1', 'parentId': None, 'childrenIds': ['a1'], 'role': 'user', 'content': 'Project Zonnebloem'},
        'a1': {
            'id': 'a1',
            'parentId': 'u1',
            'childrenIds': [],
            'role': 'assistant',
            'content': 'Genoteerd.',
            'done': True,
            'meta': {'agent_v2': BOOKMARK, 'answered_model': 'llm'},
        },
    }
    return {'title': 't', 'history': {'messages': messages, 'currentId': 'a1'}}


def seed_shared_chat(seeded) -> str:
    from open_webui.models.access_grants import AccessGrants
    from open_webui.models.chat_messages import ChatMessages
    from open_webui.models.chats import Chat, Chats
    from open_webui.models.shared_chats import SharedChats

    async def apply():
        async with seeded.sessions() as db:
            db.add(
                Chat(id='target', user_id='owner', title='t', chat=conversation(), meta={}, created_at=1, updated_at=1)
            )
            await db.commit()
            for message_id, message in conversation()['history']['messages'].items():
                await ChatMessages.upsert_message(
                    message_id=message_id, chat_id='target', user_id='owner', data=message
                )
            record = await SharedChats.create('target', 'owner', db=db)
            await Chats.update_chat_share_id_by_id('target', record.id, db=db)
            await AccessGrants.set_access_grants('shared_chat', 'target', GRANTS, db=db)
            return record.id

    return seeded.run(apply)


def stored_meta(seeded, chat_id: str, message_id: str) -> dict:
    """The message's meta as the next agent turn reads it."""
    from open_webui.models.chats import Chats

    return seeded.run(Chats.get_message_by_id_and_message_id, chat_id, message_id)['meta']


@pytest.mark.parametrize('principal', ('reader', 'admin'))
def test_a_clone_of_another_users_chat_bookmarks_no_thread(seeded, principal):
    share_id = seed_shared_chat(seeded)

    response = seeded.client.post(f'/api/v1/chats/{share_id}/clone/shared', headers=seeded.headers(principal))

    assert response.status_code == 200, response.text
    clone = response.json()
    assert clone['user_id'] == principal
    messages = clone['chat']['history']['messages']
    assert [messages[key]['content'] for key in ('u1', 'a1')] == ['Project Zonnebloem', 'Genoteerd.']
    assert messages['a1']['meta'] == {'answered_model': 'llm'}
    assert stored_meta(seeded, clone['id'], 'a1') == {'answered_model': 'llm'}
    assert stored_meta(seeded, 'target', 'a1')['agent_v2'] == BOOKMARK


def test_the_owners_clone_of_their_own_shared_chat_keeps_its_threads(seeded):
    share_id = seed_shared_chat(seeded)

    response = seeded.client.post(f'/api/v1/chats/{share_id}/clone/shared', headers=seeded.headers('owner'))

    assert response.status_code == 200, response.text
    assert response.json()['chat']['history']['messages']['a1']['meta']['agent_v2'] == BOOKMARK
