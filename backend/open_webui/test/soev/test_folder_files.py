"""A shared folder's files live in its own collection, which follows the folder's audience and files."""

import json
from types import SimpleNamespace

import pytest
from open_webui.soev import acting, folder_files, folder_sharing, ingest
from open_webui.test.soev.fake_api import FakeSoevApi

KEY = ingest.folder_collection_key('project')


def upload(file_id: str, owner: str = 'dana', **meta) -> SimpleNamespace:
    return SimpleNamespace(id=file_id, user_id=owner, filename=f'{file_id}.pdf', meta={'status': 'completed', **meta})


@pytest.fixture
def folder(chat_http: FakeSoevApi, monkeypatch: pytest.MonkeyPatch):
    """Dana's folder `project`: its file entries, its grants, the stored files and the submissions."""
    state = SimpleNamespace(api=chat_http, entries=[], grants=[], files={}, submitted=[], exists=True)

    async def get_folder(folder_id):
        if not state.exists or folder_id != 'project':
            return None
        return SimpleNamespace(id='project', user_id='dana', parent_id=None, data={'files': state.entries})

    async def view(owner_id):
        return folder_sharing.View(
            parents={'project': None} if state.exists else {},
            files={'project': state.entries} if state.exists else {},
            grants={'project': state.grants} if state.exists else {},
        )

    async def user(user_id, db=None):
        return SimpleNamespace(id=user_id, role='user')

    async def permitted(owner, resource_type, principals):
        return principals - {('user', '*')}

    async def get_files(ids):
        return [state.files[file_id] for file_id in ids if file_id in state.files]

    async def submit(record, *, collection_key, user_id, client):
        state.submitted.append((record.id, collection_key, user_id))
        return 'job-1'

    monkeypatch.setattr(folder_files.Folders, 'get_folder_by_id', get_folder)
    monkeypatch.setattr(folder_files.Users, 'get_user_by_id', user)
    monkeypatch.setattr(folder_files.Files, 'get_files_by_ids', get_files)
    monkeypatch.setattr(folder_files.ingest, 'submit', submit)
    monkeypatch.setattr(folder_sharing, 'view', view)
    monkeypatch.setattr(folder_sharing, 'permitted', permitted)
    monkeypatch.setattr(
        acting, '_requester_ref', __import__('contextvars').ContextVar('test_requester', default='owui:user:dana')
    )
    original = __import__('open_webui.soev.client', fromlist=['SoevClient']).SoevClient._request

    async def guarded(self, *args, **kwargs):
        assert kwargs.get('as_user') in (None, acting.requester_ref())
        return await original(self, *args, **kwargs)

    monkeypatch.setattr('open_webui.soev.client.SoevClient._request', guarded)
    return state


def entry(file_id: str) -> dict:
    return {'type': 'file', 'id': file_id}


def grant(principal_type: str, principal_id: str, permission: str = 'read') -> dict:
    return {'principal_type': principal_type, 'principal_id': principal_id, 'permission': permission}


def writes(api: FakeSoevApi) -> list[tuple[str, str]]:
    return [
        (request.method, request.url.path)
        for request in api.requests
        if request.method != 'GET' and request.url.path != '/v1/identity/links'
    ]


@pytest.mark.asyncio
async def test_a_shared_folder_gets_a_collection_its_members_read_holding_the_owners_uploads(folder):
    folder.entries = [entry('f-dana'), entry('f-erik'), {'type': 'collection', 'id': 'kb-local'}]
    folder.grants = [grant('user', 'frank', 'write'), grant('group', 'team')]
    folder.files = {'f-dana': upload('f-dana'), 'f-erik': upload('f-erik', owner='erik')}
    await folder_files.sync('project')
    collection = folder.api.collections[KEY]
    assert collection['created_by'] == 'owui:user:dana'
    assert {'owui:user:frank', 'owui:group:team', 'owui:user:dana'} <= set(collection['principals'])
    assert collection['writers'] == ['owui:user:dana']
    assert folder.submitted == [('f-dana', KEY, 'dana')]


@pytest.mark.asyncio
async def test_a_folder_that_is_not_shared_gets_no_collection(folder):
    folder.entries = [entry('f-dana')]
    folder.files = {'f-dana': upload('f-dana')}
    await folder_files.sync('project')
    assert KEY not in folder.api.collections
    assert writes(folder.api) == []


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['unshared', 'no-files-left', 'deleted'])
async def test_the_collection_goes_when_the_folder_is_unshared_emptied_or_deleted(folder, change):
    folder.entries = [entry('f-dana')]
    folder.grants = [grant('user', 'frank')]
    folder.files = {'f-dana': upload('f-dana')}
    await folder_files.sync('project')
    if change == 'unshared':
        folder.grants = []
    elif change == 'no-files-left':
        folder.entries = []
    else:
        folder.exists = False
    folder.api.requests.clear()
    await folder_files.sync('project')
    assert writes(folder.api) == [('DELETE', f'/v1/collections/{KEY}')]


@pytest.mark.asyncio
async def test_narrowing_takes_away_readers_and_files_and_never_adds(folder):
    folder.entries = [entry('f-1'), entry('f-2')]
    folder.grants = [grant('user', 'frank'), grant('user', 'gwen')]
    folder.files = {'f-1': upload('f-1'), 'f-2': upload('f-2')}
    await folder_files.sync('project')
    folder.api.add_document(KEY, 'f-1')
    folder.api.add_document(KEY, 'f-2')
    folder.api.requests.clear()

    await folder_files.narrow('dana', 'project', {('user', 'gwen'), ('user', 'hugo')}, ['f-1'])

    put = next(request for request in folder.api.requests if request.method == 'PUT')
    body = json.loads(put.content)
    assert 'owui:user:frank' not in body['principals'] and 'owui:user:hugo' not in body['principals']
    assert {'owui:user:gwen', 'owui:user:dana'} <= set(body['principals'])
    assert ('DELETE', f'/v1/collections/{KEY}/documents/f-2') in writes(folder.api)
    assert ('DELETE', f'/v1/collections/{KEY}/documents/f-1') not in writes(folder.api)


@pytest.mark.asyncio
async def test_narrowing_a_folder_that_will_not_be_shared_deletes_its_collection(folder):
    folder.entries = [entry('f-1')]
    folder.grants = [grant('user', 'frank')]
    folder.files = {'f-1': upload('f-1')}
    await folder_files.sync('project')
    folder.api.requests.clear()
    await folder_files.narrow('dana', 'project', set(), ['f-1'])
    assert writes(folder.api) == [('DELETE', f'/v1/collections/{KEY}')]


def test_folder_collections_are_internal():
    assert ingest.is_folder_collection(KEY)
    assert not ingest.is_assistant_collection(KEY) and not ingest.is_attachments_collection(KEY)
