"""A File's storage path (bucket and object key) never reaches the browser or embedded chat data."""

import json

import httpx
import pytest

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


@pytest.mark.parametrize('platform_document', [False, True])
def test_file_routes_never_return_the_storage_path(seeded, monkeypatch, platform_document):
    from open_webui import config
    from open_webui.models.files import File
    from open_webui.models.users import User
    from open_webui.soev import acting, client
    from open_webui.test.soev.fake_api import FakeSoevApi, person_ref

    fake = FakeSoevApi()
    monkeypatch.setattr(config, 'SOEV_API_KEY', 'soev_test_cred-runtime_test-secret')
    monkeypatch.setattr(config, 'SOEV_API_SERVICE_PRINCIPAL', 'owui:service:webui')
    monkeypatch.setattr(config, 'SOEV_TRANSFER_STORE_HOST', 'store.invalid')
    with Session(seeded.engine) as db:
        db.get(User, 'owner').person_id = person_ref('owner')
        db.commit()

    def resolver(request, user, auth_token):
        async def token(**kwargs):
            return f'fictional-login:owui:user:{user.id}'

        return token

    monkeypatch.setattr(acting, 'oidc_token_resolver', resolver)
    payload = b'hello' if platform_document else b'<svg xmlns="http://www.w3.org/2000/svg"></svg>'
    suffix, mime = ('txt', 'text/plain') if platform_document else ('svg', 'image/svg+xml')

    def handle(request):
        if request.url.path.endswith('/original'):
            # Authenticate and authorize the original using the real fake's document read.
            checked = fake.handle(
                httpx.Request('GET', str(request.url).removesuffix('/original'), headers=request.headers)
            )
            if checked.status_code != 200:
                return checked
            return httpx.Response(200, content=payload)
        return fake.handle(request)

    original_client = httpx.AsyncClient
    remote = original_client(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(client, '_shared_client', lambda: remote)

    def transport_client(**kwargs):
        kwargs.setdefault('transport', httpx.MockTransport(handle))
        return original_client(**kwargs)

    monkeypatch.setattr(httpx, 'AsyncClient', transport_client)
    headers = seeded.headers('owner')
    try:
        response = seeded.client.post(
            '/api/v1/files/?process=false',
            files={'file': (f'notes.{suffix}', payload, mime)},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        file_id = response.json()['id']
        assert 'path' not in response.json()
        with Session(seeded.engine) as db:
            row = db.get(File, file_id)
            if platform_document:
                assert row.path is None
                assert list(fake.uploads.values()) == [payload]
                for job_id in fake.jobs:
                    fake.advance(job_id, 'SUCCEEDED')
            else:
                assert row.path

        responses = [
            seeded.client.get(f'/api/v1/files/{file_id}', headers=headers),
            seeded.client.post(
                f'/api/v1/files/{file_id}/rename', json={'filename': f'renamed.{suffix}'}, headers=headers
            ),
            seeded.client.get('/api/v1/files/', headers=headers),
            seeded.client.get(f'/api/v1/files/search?filename=*.{suffix}', headers=headers),
        ]
        for listed in responses:
            assert listed.status_code == 200, listed.text
            assert '"path"' not in listed.text
        # Both local media and platform originals are served by id without exposing storage paths.
        content = seeded.client.get(f'/api/v1/files/{file_id}/content', headers=headers)
        assert content.status_code == 200, content.text
        assert content.content == payload
    finally:
        seeded.run(remote.aclose)


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


def test_folder_and_assistant_data_drop_embedded_paths():
    from open_webui.models.folders import FolderForm, FolderUpdateForm
    from open_webui.models.models import ModelMeta

    files = {'files': [{'type': 'file', 'id': 'f1', 'file': file_record()}]}
    for model in (
        FolderForm(name='Map', data=files),
        FolderUpdateForm(data=files),
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
