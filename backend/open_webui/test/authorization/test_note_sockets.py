"""Note socket rooms follow the note's current access, and only note documents can be opened."""

from unittest.mock import AsyncMock

import pytest
import socketio
from open_webui.socket import main as socket_main
from open_webui.socket.utils import YdocManager
from sqlalchemy import delete

from .test_matrix import GRANTS, seed_resource

NOTE_ROOMS = ('note:target', 'doc_note:target')
WRITERS_ONLY = [grant for grant in GRANTS if grant['principal_id'] == 'writers']


@pytest.fixture
def sockets(seeded, monkeypatch):
    manager = socketio.AsyncManager()
    manager.set_server(socket_main.sio)
    manager.initialize()
    monkeypatch.setattr(socket_main.sio, 'manager', manager)
    pool = {}
    monkeypatch.setattr(socket_main, 'SESSION_POOL', pool)

    async def session_user(sid):
        return pool.get(sid)

    monkeypatch.setattr(socket_main, 'get_socket_session_user', session_user)
    sent = []

    async def emit(event, data, room=None, to=None, skip_sid=None, **kwargs):
        target = room or to
        sent.append((event, data, target, set(socket_main.get_session_ids_from_room(target)) - {skip_sid}))

    monkeypatch.setattr(socket_main.sio, 'emit', emit)
    monkeypatch.setattr(socket_main, 'YDOC_MANAGER', YdocManager(redis=None, redis_key_prefix='test'))
    monkeypatch.setattr(socket_main, 'stop_item_tasks', AsyncMock())
    monkeypatch.setattr(socket_main, 'create_task', AsyncMock())
    seeded.run(seed_resource, seeded, 'note')

    def connect(principal, pooled=True):
        sid = seeded.run(manager.connect, f'eio-{principal}', '/')
        if pooled:
            pool[sid] = {'id': principal, 'role': 'user'}
        return sid

    def token(principal):
        return seeded.headers(principal)['Authorization'].removeprefix('Bearer ')

    def open_note(principal):
        sid = connect(principal)
        seeded.run(socket_main.join_note, sid, {'note_id': 'target', 'auth': {'token': token(principal)}})
        seeded.run(socket_main.ydoc_document_join, sid, {'document_id': 'note:target'})
        assert all(room in socket_main.sio.rooms(sid) for room in NOTE_ROOMS)
        return sid

    def members(room):
        return set(socket_main.get_session_ids_from_room(room))

    class Sockets:
        pass

    sockets = Sockets()
    sockets.connect, sockets.token, sockets.open_note, sockets.members, sockets.sent = (
        connect,
        token,
        open_note,
        members,
        sent,
    )
    return sockets


def test_unsharing_a_note_evicts_readers_who_lost_access(seeded, sockets):
    owner, reader, writer = (sockets.open_note(name) for name in ('owner', 'reader', 'writer'))
    response = seeded.client.post(
        '/api/v1/notes/target/access/update', headers=seeded.headers('owner'), json={'access_grants': WRITERS_ONLY}
    )
    assert response.status_code == 200, response.text
    for room in NOTE_ROOMS:
        assert sockets.members(room) == {owner, writer}
    assert reader not in seeded.run(socket_main.YDOC_MANAGER.get_users, 'note:target')


def test_a_note_update_that_unshares_reaches_only_remaining_readers(seeded, sockets):
    owner = sockets.open_note('owner')
    sockets.open_note('reader')
    response = seeded.client.post(
        '/api/v1/notes/target/update',
        headers=seeded.headers('owner'),
        json={'title': 'private now', 'access_grants': WRITERS_ONLY},
    )
    assert response.status_code == 200, response.text
    [(_, data, _, recipients)] = [sent for sent in sockets.sent if sent[0] == 'events:note']
    assert data['title'] == 'private now'
    assert recipients == {owner}


def test_deleting_a_note_empties_its_rooms(seeded, sockets):
    sockets.open_note('owner')
    sockets.open_note('reader')
    response = seeded.client.delete('/api/v1/notes/target/delete', headers=seeded.headers('owner'))
    assert response.status_code == 200, response.text
    for room in NOTE_ROOMS:
        assert sockets.members(room) == set()


def test_state_and_awareness_recheck_access_revoked_outside_the_note(seeded, sockets):
    from open_webui.models.groups import GroupMember

    async def leave_readers_group():
        async with seeded.sessions() as db:
            await db.execute(delete(GroupMember).filter_by(group_id='readers', user_id='reader'))
            await db.commit()

    reader = sockets.open_note('reader')
    seeded.run(leave_readers_group)
    sockets.sent.clear()
    seeded.run(socket_main.yjs_awareness_update, reader, {'document_id': 'note:target', 'update': [1]})
    seeded.run(socket_main.yjs_document_state, reader, {'document_id': 'note:target'})
    assert sockets.sent == []
    assert not any(room in socket_main.sio.rooms(reader) for room in NOTE_ROOMS)


def test_only_note_documents_can_be_joined_or_updated(seeded, sockets):
    sid = sockets.connect('outsider')
    seeded.run(socket_main.ydoc_document_join, sid, {'document_id': 'victim-chat'})
    assert 'doc_victim-chat' not in socket_main.sio.rooms(sid)
    assert sockets.sent == []

    # Even inside such a room an update cannot reach another user's chat tasks.
    seeded.run(socket_main.sio.enter_room, sid, 'doc_victim-chat')
    seeded.run(socket_main.yjs_document_update, sid, {'document_id': 'victim-chat', 'update': [1], 'data': {'md': 'x'}})
    socket_main.stop_item_tasks.assert_not_awaited()
    assert sockets.sent == []


def test_join_note_needs_the_sockets_own_session(seeded, sockets):
    unpooled = sockets.connect('reader', pooled=False)
    seeded.run(socket_main.join_note, unpooled, {'note_id': 'target', 'auth': {'token': sockets.token('reader')}})
    assert 'note:target' not in socket_main.sio.rooms(unpooled)

    impersonating = sockets.connect('outsider')
    seeded.run(socket_main.join_note, impersonating, {'note_id': 'target', 'auth': {'token': sockets.token('reader')}})
    assert 'note:target' not in socket_main.sio.rooms(impersonating)
