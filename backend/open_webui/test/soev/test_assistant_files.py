"""An assistant's files collection follows the assistant: its access, its files, and its deletion."""

from types import SimpleNamespace

import pytest
from open_webui.models.access_grants import AccessGrantModel
from open_webui.models.models import ModelMeta, ModelModel, ModelParams
from open_webui.soev import acting, assistant_files, ingest
from open_webui.test.soev.fake_api import FakeSoevApi, person_ref

KEY = ingest.assistant_collection_key('helper')


def grant(principal_type: str, principal_id: str, permission: str = 'read') -> AccessGrantModel:
    return AccessGrantModel(
        id=f'{principal_type}-{principal_id}-{permission}',
        resource_type='model',
        resource_id='helper',
        principal_type=principal_type,
        principal_id=principal_id,
        permission=permission,
        created_at=1,
    )


def assistant(*file_ids: str, grants: list[AccessGrantModel] | None = None) -> ModelModel:
    """Bob's assistant `helper`, with a knowledge base and the files `file_ids`."""
    knowledge = [{'id': 'kb-a', 'type': 'collection'}, *({'id': file_id, 'type': 'file'} for file_id in file_ids)]
    return ModelModel(
        id='helper',
        user_id='bob',
        base_model_id='llm',
        name='Helper',
        params=ModelParams(),
        meta=ModelMeta(knowledge=knowledge),
        access_grants=grants or [],
        is_active=True,
        updated_at=1,
        created_at=1,
    )


def file(file_id: str, owner: str = 'bob', **meta) -> SimpleNamespace:
    return SimpleNamespace(id=file_id, user_id=owner, filename=f'{file_id}.txt', meta={'status': 'completed', **meta})


@pytest.fixture
def saved(chat_http: FakeSoevApi, monkeypatch: pytest.MonkeyPatch):
    """The stored assistant and files, and the ingest submissions as (file id, collection, submitter)."""
    state = SimpleNamespace(api=chat_http, model=None, files={}, submitted=[])

    async def get_model(model_id):
        return state.model if state.model is not None and state.model.id == model_id else None

    async def get_files(ids):
        return [state.files[file_id] for file_id in ids if file_id in state.files]

    async def submit(record, *, collection_key, user_id, client):
        if (record.meta or {}).get('soev_job'):
            raise ingest.IngestBusy(record.id)
        state.submitted.append((record.id, collection_key, user_id))
        return 'job-1'

    monkeypatch.setattr(assistant_files.Models, 'get_model_by_id', get_model)
    monkeypatch.setattr(assistant_files.Files, 'get_files_by_ids', get_files)
    monkeypatch.setattr(assistant_files.ingest, 'submit', submit)
    monkeypatch.setattr(
        acting, '_requester_ref', __import__('contextvars').ContextVar('test_requester', default='owui:user:bob')
    )
    from open_webui.soev.client import SoevClient

    original = SoevClient._request

    async def guarded(self, *args, **kwargs):
        assert kwargs.get('as_user') in (None, acting.requester_ref())
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(SoevClient, '_request', guarded)
    return state


@pytest.mark.asyncio
async def test_a_saved_assistant_gets_a_collection_its_users_read_and_its_files_are_submitted_as_its_owner(saved):
    saved.model = assistant('f1', 'f2', grants=[grant('group', 'juristen'), grant('user', 'carol', 'write')])
    saved.files = {'f1': file('f1'), 'f2': file('f2')}
    await assistant_files.sync('helper')
    collection = saved.api.collections[KEY]
    assert collection['created_by'] == person_ref('bob')
    assert {'platform:group:juristen', person_ref('carol'), person_ref('bob')} <= set(collection['principals'])
    assert collection['writers'] == sorted([person_ref('bob'), person_ref('carol')])
    assert ingest.is_assistant_collection(KEY) and not ingest.is_attachments_collection(KEY)
    assert saved.submitted == [('f1', KEY, 'bob'), ('f2', KEY, 'bob')]


@pytest.mark.asyncio
@pytest.mark.parametrize('unbound', ['bob', 'carol'])
async def test_an_assistant_requires_person_bindings_for_its_owner_and_shared_users(saved, monkeypatch, unbound):
    from open_webui.models.users import Users

    get_user = Users.get_user_by_id

    async def user(user_id, db=None):
        return SimpleNamespace(person_id=None) if user_id == unbound else await get_user(user_id)

    monkeypatch.setattr(Users, 'get_user_by_id', user)
    saved.model = assistant('f1', grants=[grant('user', 'carol', 'write')])
    saved.files = {'f1': file('f1')}
    with pytest.raises(ValueError, match='no valid platform person binding'):
        await assistant_files.sync('helper')
    assert saved.api.requests == []
    assert saved.submitted == []


@pytest.mark.asyncio
async def test_a_resync_republishes_access_submits_only_whats_missing_and_drops_removed_files(saved):
    saved.model = assistant('f1', 'f2')
    saved.files = {'f1': file('f1'), 'f2': file('f2'), 'f3': file('f3')}
    await assistant_files.sync('helper')
    saved.submitted.clear()
    saved.api.add_document(KEY, 'f1')
    saved.api.add_document(KEY, 'old')
    saved.model = assistant('f1', 'f3', grants=[grant('user', '*')])
    saved.api.requests.clear()
    await assistant_files.sync('helper')
    assert saved.submitted == [('f3', KEY, 'bob')]
    writes = [
        (request.method, request.url.path)
        for request in saved.api.requests
        if request.method != 'GET' and request.url.path != '/v1/identity/links'
    ]
    assert ('PUT', f'/v1/collections/{KEY}/access') in writes
    assert ('DELETE', f'/v1/collections/{KEY}/documents/old') in writes
    assert ('DELETE', f'/v1/collections/{KEY}/documents/f1') not in writes


@pytest.mark.asyncio
async def test_a_file_ingesting_or_failed_there_is_not_submitted_and_one_ingesting_elsewhere_is_waited_for(saved):
    saved.model = assistant('busy', 'broken', 'elsewhere')
    saved.files = {
        'busy': file('busy', soev_job={'job_id': 'j', 'collection_key': ingest.attachments_collection_key('bob')}),
        'broken': file('broken', status='failed', soev_collection_key=KEY),
        # Failed in the owner's chat attachments, not here: worth a try in the assistant's collection.
        'elsewhere': file('elsewhere', status='failed', soev_collection_key=ingest.attachments_collection_key('bob')),
    }
    assert await assistant_files.sync('helper') is True
    assert saved.submitted == [('elsewhere', KEY, 'bob')]
    saved.files['busy'].meta['soev_job']['collection_key'] = KEY
    saved.submitted.clear()
    saved.api.add_document(KEY, 'elsewhere')
    assert await assistant_files.sync('helper') is False
    assert saved.submitted == []


@pytest.mark.asyncio
@pytest.mark.parametrize('model', [None, assistant()], ids=['deleted', 'no-files-left'])
async def test_the_collection_of_a_deleted_or_fileless_assistant_is_deleted(saved, model):
    saved.model = assistant('f1')
    saved.files = {'f1': file('f1')}
    await assistant_files.sync('helper')
    saved.model = model
    saved.api.requests.clear()
    await assistant_files.sync('helper')
    deletes = [request.url.path for request in saved.api.requests if request.method == 'DELETE']
    assert deletes == [f'/v1/collections/{KEY}']


@pytest.mark.asyncio
async def test_someone_elses_file_is_never_ingested_and_its_copy_and_a_deleted_files_are_dropped(saved):
    saved.model = assistant('mine', 'theirs', 'deleted')
    saved.files = {'mine': file('mine'), 'theirs': file('theirs', owner='alice')}
    await assistant_files.sync('helper')
    saved.api.add_document(KEY, 'mine')
    saved.api.add_document(KEY, 'theirs')
    saved.api.add_document(KEY, 'deleted')
    saved.submitted.clear()
    saved.api.requests.clear()
    await assistant_files.sync('helper')
    assert saved.submitted == []
    assert saved.files['theirs'].meta == {'status': 'completed'}
    deletes = {request.url.path for request in saved.api.requests if request.method == 'DELETE'}
    assert deletes == {f'/v1/collections/{KEY}/documents/theirs', f'/v1/collections/{KEY}/documents/deleted'}


@pytest.mark.asyncio
async def test_an_assistant_holding_only_someone_elses_files_has_no_collection(saved):
    saved.model = assistant('theirs')
    saved.files = {'theirs': file('theirs', owner='alice')}
    await assistant_files.sync('helper')
    assert KEY not in saved.api.collections
    assert saved.submitted == []


@pytest.mark.asyncio
async def test_an_assistant_without_files_never_creates_a_collection(saved):
    saved.model = assistant()
    await assistant_files.sync('helper')
    assert KEY not in saved.api.collections
    assert not [
        request
        for request in saved.api.requests
        if request.method != 'GET' and request.url.path != '/v1/identity/links'
    ]


def test_a_long_model_id_gets_a_collection_key_soev_api_accepts():
    long_id = 'x' * 256
    assert ingest.assistant_collection_key('helper') == 'owui-assistant-helper'
    assert len(ingest.assistant_collection_key(long_id)) <= 128
    assert ingest.assistant_collection_key(long_id) != ingest.assistant_collection_key(long_id + 'y')
