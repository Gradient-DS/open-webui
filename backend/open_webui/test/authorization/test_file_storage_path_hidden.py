"""A File's storage path (bucket and object key) never reaches the browser or embedded chat data."""

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

STORAGE_PATH = 's3://acme-tenant-uploads/f1_offerte.pdf'


def file_record(path=STORAGE_PATH):
    return {
        'id': 'f1',
        'user_id': 'owner',
        'hash': None,
        'filename': 'offerte.pdf',
        'path': path,
        'data': {},
        'meta': {'name': 'offerte.pdf', 'content_type': 'application/pdf', 'size': 3},
        'created_at': 1,
        'updated_at': 1,
    }


def chat_body():
    files = [{'type': 'file', 'id': 'f1', 'name': 'offerte.pdf', 'url': '/api/v1/files/f1', 'file': file_record()}]
    message = {'id': 'm1', 'parentId': None, 'childrenIds': [], 'role': 'user', 'content': 'hoi', 'files': files}
    return {
        'title': 'Offerte',
        'files': files,
        'history': {'currentId': 'm1', 'messages': {'m1': message}},
        'messages': [message],
    }


def assert_no_storage_path(text):
    assert 'acme-tenant-uploads' not in text


def stored(seeded, model, key, column):
    with Session(seeded.engine) as db:
        return json.dumps(getattr(db.get(model, key), column))


def enable_sharing(seeded):
    from open_webui.models.config import Config

    async def apply():
        permissions = await Config.get('user.permissions')
        permissions['chat']['share'] = True
        await Config.upsert({'user.permissions': permissions})

    seeded.run(apply)


def test_file_routes_never_return_the_storage_path(seeded):
    headers = seeded.headers('owner')
    response = seeded.client.post(
        '/api/v1/files/?process=false',
        files={'file': ('notes.txt', b'hello', 'text/plain')},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    file_id = response.json()['id']
    assert 'path' not in response.json()

    responses = [
        seeded.client.get(f'/api/v1/files/{file_id}', headers=headers),
        seeded.client.post(f'/api/v1/files/{file_id}/rename', json={'filename': 'renamed.txt'}, headers=headers),
        seeded.client.get('/api/v1/files/', headers=headers),
        seeded.client.get('/api/v1/files/search?filename=*.txt', headers=headers),
    ]
    for listed in responses:
        assert listed.status_code == 200, listed.text
        assert '"path"' not in listed.text
    # The row keeps its path; content is served by id.
    content = seeded.client.get(f'/api/v1/files/{file_id}/content', headers=headers)
    assert content.status_code == 200, content.text
    assert content.content == b'hello'


def test_chat_writes_reads_shares_and_exports_drop_embedded_paths(seeded):
    from open_webui.models.chat_messages import ChatMessage
    from open_webui.models.chats import Chat
    from open_webui.models.shared_chats import SharedChat

    enable_sharing(seeded)
    headers = seeded.headers('owner')
    created = seeded.client.post('/api/v1/chats/new', json={'chat': chat_body()}, headers=headers)
    assert created.status_code == 200, created.text
    chat_id = created.json()['id']
    assert_no_storage_path(created.text)
    assert_no_storage_path(stored(seeded, Chat, chat_id, 'chat'))
    with Session(seeded.engine) as db:
        rows = db.execute(select(ChatMessage.files).where(ChatMessage.chat_id == chat_id)).scalars().all()
    assert rows and all('offerte.pdf' in json.dumps(files) for files in rows)
    assert_no_storage_path(json.dumps(rows))

    updated = seeded.client.post(f'/api/v1/chats/{chat_id}', json={'chat': chat_body()}, headers=headers)
    assert updated.status_code == 200, updated.text
    assert_no_storage_path(stored(seeded, Chat, chat_id, 'chat'))

    shared = seeded.client.post(f'/api/v1/chats/{chat_id}/share', headers=headers)
    assert shared.status_code == 200, shared.text
    share_id = shared.json()['share_id']
    assert_no_storage_path(stored(seeded, SharedChat, share_id, 'chat'))

    for path in (f'/api/v1/chats/{chat_id}', f'/api/v1/chats/share/{share_id}', '/api/v1/chats/all'):
        response = seeded.client.get(path, headers=headers)
        assert response.status_code == 200, response.text
        assert 'offerte.pdf' in response.text
        assert_no_storage_path(response.text)


def test_share_snapshot_of_a_legacy_row_drops_embedded_paths(seeded):
    from open_webui.models.chats import Chat
    from open_webui.models.shared_chats import SharedChat

    enable_sharing(seeded)
    with Session(seeded.engine) as db:
        db.add(Chat(id='legacy', user_id='owner', title='t', chat=chat_body(), meta={}, created_at=1, updated_at=1))
        db.commit()
    response = seeded.client.post('/api/v1/chats/legacy/share', headers=seeded.headers('owner'))
    assert response.status_code == 200, response.text
    assert_no_storage_path(stored(seeded, SharedChat, response.json()['share_id'], 'chat'))


def test_folder_channel_and_assistant_data_drop_embedded_paths():
    from open_webui.models.folders import FolderForm, FolderUpdateForm
    from open_webui.models.messages import MessageForm
    from open_webui.models.models import ModelMeta

    files = {'files': [{'type': 'file', 'id': 'f1', 'file': file_record()}]}
    for model in (
        FolderForm(name='Map', data=files),
        FolderUpdateForm(data=files),
        MessageForm(content='hoi', data=files),
        ModelMeta(knowledge=files['files']),
    ):
        dumped = model.model_dump_json()
        assert 'offerte.pdf' in dumped
        assert_no_storage_path(dumped)


def test_strip_keeps_unrelated_paths_and_returns_untouched_values():
    from open_webui.models.files import strip_file_paths

    untouched = {
        'files': [{'type': 'filesystem', 'path': '/workspace/a.txt'}],
        'file': {'id': 'x', 'path': 'docs/guide.md'},
    }
    assert strip_file_paths(untouched) is untouched
    stripped = strip_file_paths({'file': file_record(), 'files': [file_record()]})
    assert 'path' not in stripped['file'] and 'path' not in stripped['files'][0]
