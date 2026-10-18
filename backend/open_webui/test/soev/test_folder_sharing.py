"""Sharing a folder shares its owner's local knowledge bases and notes, and unsharing takes back only that."""

from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from open_webui.models.access_grants import AccessGrantModel
from open_webui.soev import acting, folder_sharing
from open_webui.soev.client import SoevApiError


def grant(principal_type: str, principal_id: str, permission: str = 'read') -> dict:
    return {'principal_type': principal_type, 'principal_id': principal_id, 'permission': permission}


def model(resource_type: str, resource_id: str, row: dict) -> AccessGrantModel:
    return AccessGrantModel(id='g', resource_type=resource_type, resource_id=resource_id, created_at=1, **row)


class Ledger:
    """`FolderShareGrants` in memory."""

    def __init__(self):
        self.rows: set[tuple[str, str, str, str, str]] = set()

    async def get_by_owner(self, owner_id):
        return [
            SimpleNamespace(resource_type=row[1], resource_id=row[2], principal_type=row[3], principal_id=row[4])
            for row in self.rows
            if row[0] == owner_id
        ]

    async def record(self, owner_id, resource_type, resource_id, principals):
        self.rows |= {(owner_id, resource_type, resource_id, *principal) for principal in principals}

    async def forget(self, resource_type, resource_id, principals):
        self.rows = {
            row
            for row in self.rows
            if (row[1], row[2], row[3], row[4])
            not in {(resource_type, resource_id, *principal) for principal in principals}
        }


@pytest.fixture
def world(monkeypatch):
    """Dana's folders, knowledge bases and notes; every grant write is recorded with the ref it was asserted as."""
    state = SimpleNamespace(
        folders={},
        folder_grants={},
        grants={},
        knowledge={},
        notes={},
        writes=[],
        public_allowed=False,
        fail_writes=False,
        ledger=Ledger(),
    )

    async def folders_of(owner_id):
        return [folder for folder in state.folders.values() if folder.user_id == owner_id]

    async def folder_grants(resource_type, ids, db=None):
        return {
            folder_id: [model('folder', folder_id, row) for row in state.folder_grants.get(folder_id, [])]
            for folder_id in ids
        }

    async def note_grants(resource_type, resource_id, db=None):
        return [model(resource_type, resource_id, row) for row in state.grants.get((resource_type, resource_id), [])]

    async def set_grants(resource_type, resource_id, grants, db=None):
        if state.fail_writes:
            raise SoevApiError(503, 'injected_failure', 'injected_failure')
        state.writes.append((resource_type, resource_id, acting.acting_ref()))
        state.grants[(resource_type, resource_id)] = list(grants)

    async def knowledge_by_id(key):
        kb = state.knowledge.get(key)
        if kb is None:
            return None
        rows = state.grants.get(('knowledge', key), [])
        return SimpleNamespace(**kb, access_grants=[model('knowledge', key, row) for row in rows])

    async def note_by_id(note_id, db=None):
        return state.notes.get(note_id)

    async def user_by_id(user_id, db=None):
        return SimpleNamespace(id=user_id, role='user')

    async def permissions(key, default=None):
        return {}

    async def allowed(defaults, user_id, role, grants, key, *args, **kwargs):
        return grants if state.public_allowed else []

    monkeypatch.setattr(folder_sharing, 'configured', lambda: True)
    monkeypatch.setattr(folder_sharing, 'FolderShareGrants', state.ledger)
    monkeypatch.setattr(folder_sharing.Folders, 'get_folders_by_user_id', folders_of)
    monkeypatch.setattr(folder_sharing.AccessGrants, 'get_grants_by_resources', folder_grants)
    monkeypatch.setattr(folder_sharing.AccessGrants, 'get_grants_by_resource', note_grants)
    monkeypatch.setattr(folder_sharing.AccessGrants, 'set_access_grants', set_grants)
    monkeypatch.setattr(folder_sharing.Knowledges, 'get_knowledge_by_id', knowledge_by_id)
    monkeypatch.setattr(folder_sharing.Notes, 'get_note_by_id', note_by_id)
    monkeypatch.setattr(folder_sharing.Users, 'get_user_by_id', user_by_id)
    monkeypatch.setattr(folder_sharing.Config, 'get', permissions)
    monkeypatch.setattr(folder_sharing, 'filter_allowed_access_grants', allowed)
    monkeypatch.setattr(folder_sharing, '_locks', {})
    state.narrowed, state.scheduled = [], []

    async def narrow(owner_id, folder_id, principals, files):
        state.narrowed.append((folder_id, principals, files, dict(state.folder_grants)))

    monkeypatch.setattr(folder_sharing.folder_files, 'narrow', narrow)
    monkeypatch.setattr(folder_sharing.folder_files, 'schedule', state.scheduled.append)

    state.knowledge = {
        'kb-local': {'user_id': 'dana', 'type': 'local', 'name': 'Beleid'},
        'kb-cloud': {'user_id': 'dana', 'type': 'onedrive', 'name': 'OneDrive Projecten'},
        'kb-other': {'user_id': 'erik', 'type': 'local', 'name': 'Contracten'},
    }
    state.notes = {
        'note-1': SimpleNamespace(user_id='dana', title='Afspraken'),
        'note-erik': SimpleNamespace(user_id='erik', title='Notulen'),
    }
    entries = [
        {'type': 'collection', 'id': 'kb-local'},
        {'type': 'collection', 'id': 'kb-cloud'},
        {'type': 'collection', 'id': 'kb-other'},
        {'type': 'note', 'id': 'note-1'},
        {'type': 'note', 'id': 'note-erik'},
        {'type': 'file', 'id': 'file-1'},
    ]
    state.folders = {
        'project': SimpleNamespace(id='project', user_id='dana', parent_id=None, data={'files': entries}),
    }
    return state


async def share(world, folder_id: str, grants: list[dict]) -> None:
    def edit(folders):
        folders.grants[folder_id] = grants

    async with folder_sharing.changing('dana', edit):
        world.folder_grants[folder_id] = grants


def readers(world, resource_type: str, resource_id: str) -> set[tuple[str, str, str]]:
    return {
        (row['principal_type'], row['principal_id'], row['permission'])
        for row in world.grants.get((resource_type, resource_id), [])
    }


@pytest.mark.asyncio
async def test_sharing_a_folder_gives_its_members_read_on_the_owners_local_knowledge_base_and_note(world):
    await share(world, 'project', [grant('user', 'frank'), grant('group', 'team')])
    for resource in (('knowledge', 'kb-local'), ('note', 'note-1')):
        assert readers(world, *resource) == {('user', 'frank', 'read'), ('group', 'team', 'read')}
    assert {ref for *_, ref in world.writes} == {'owui:user:dana'}


@pytest.mark.asyncio
async def test_a_cloud_knowledge_base_and_another_owners_items_are_never_shared(world):
    await share(world, 'project', [grant('user', 'frank')])
    written = {(resource_type, resource_id) for resource_type, resource_id, _ in world.writes}
    assert written == {('knowledge', 'kb-local'), ('note', 'note-1')}


@pytest.mark.asyncio
async def test_a_folder_write_grant_gives_read_not_write(world):
    await share(world, 'project', [grant('user', 'frank', 'write')])
    assert readers(world, 'knowledge', 'kb-local') == {('user', 'frank', 'read')}
    assert readers(world, 'note', 'note-1') == {('user', 'frank', 'read')}


@pytest.mark.asyncio
async def test_unsharing_revokes_what_the_share_added_and_keeps_grants_the_item_already_had(world):
    world.grants[('knowledge', 'kb-local')] = [grant('user', 'gwen')]
    world.grants[('note', 'note-1')] = [grant('user', 'frank')]
    await share(world, 'project', [grant('user', 'frank'), grant('user', 'gwen')])
    assert readers(world, 'knowledge', 'kb-local') == {('user', 'gwen', 'read'), ('user', 'frank', 'read')}

    await share(world, 'project', [])

    assert readers(world, 'knowledge', 'kb-local') == {('user', 'gwen', 'read')}
    assert readers(world, 'note', 'note-1') == {('user', 'frank', 'read')}
    assert world.ledger.rows == set()


@pytest.mark.asyncio
async def test_removing_a_knowledge_base_from_a_shared_folder_revokes_its_share(world):
    await share(world, 'project', [grant('user', 'frank')])
    remaining = [{'type': 'note', 'id': 'note-1'}]

    def edit(folders):
        folders.files['project'] = remaining

    async with folder_sharing.changing('dana', edit):
        world.folders['project'].data = {'files': remaining}

    assert readers(world, 'knowledge', 'kb-local') == set()
    assert readers(world, 'note', 'note-1') == {('user', 'frank', 'read')}


@pytest.mark.asyncio
async def test_a_member_shared_with_directly_for_writing_since_keeps_access_after_unsharing(world):
    await share(world, 'project', [grant('user', 'frank')])
    world.grants[('knowledge', 'kb-local')] = [grant('user', 'frank'), grant('user', 'frank', 'write')]

    await share(world, 'project', [])

    assert readers(world, 'knowledge', 'kb-local') == {('user', 'frank', 'read'), ('user', 'frank', 'write')}
    assert world.ledger.rows == set()


@pytest.mark.asyncio
async def test_a_subfolder_shares_through_its_parent_until_it_moves_out(world):
    world.folders['project'].data = {'files': []}
    world.folders['drafts'] = SimpleNamespace(
        id='drafts', user_id='dana', parent_id='project', data={'files': [{'type': 'collection', 'id': 'kb-local'}]}
    )
    await share(world, 'project', [grant('user', 'frank')])
    assert readers(world, 'knowledge', 'kb-local') == {('user', 'frank', 'read')}

    def edit(folders):
        folders.parents['drafts'] = None

    async with folder_sharing.changing('dana', edit):
        world.folders['drafts'].parent_id = None

    assert readers(world, 'knowledge', 'kb-local') == set()


@pytest.mark.asyncio
async def test_a_failed_revocation_refuses_the_folder_change(world):
    await share(world, 'project', [grant('user', 'frank')])
    world.fail_writes = True
    committed = False

    with pytest.raises(SoevApiError):

        def edit(folders):
            folders.grants['project'] = []

        async with folder_sharing.changing('dana', edit):
            committed = True

    assert not committed
    assert readers(world, 'knowledge', 'kb-local') == {('user', 'frank', 'read')}


@pytest.mark.asyncio
async def test_everyone_follows_the_owners_permission_to_share_with_everyone(world):
    await share(world, 'project', [grant('user', '*'), grant('user', 'frank')])
    assert readers(world, 'knowledge', 'kb-local') == {('user', 'frank', 'read')}

    world.public_allowed = True
    await share(world, 'project', [grant('user', '*'), grant('user', 'frank')])
    assert readers(world, 'knowledge', 'kb-local') == {('user', 'frank', 'read'), ('user', '*', 'read')}


@pytest.mark.asyncio
async def test_the_folders_file_collection_is_narrowed_before_the_change_and_synced_after(world):
    await share(world, 'project', [grant('user', 'frank')])
    assert world.scheduled == ['project']
    await share(world, 'project', [])
    folder_id, principals, files, committed = world.narrowed[-1]
    assert (folder_id, principals, files) == ('project', set(), ['file-1'])
    assert committed == {'project': [grant('user', 'frank')]}
    assert world.scheduled == ['project', 'project']


@pytest.mark.asyncio
async def test_without_soev_api_a_folder_change_shares_nothing(world, monkeypatch):
    monkeypatch.setattr(folder_sharing, 'configured', lambda: False)
    await share(world, 'project', [grant('user', 'frank')])
    assert world.writes == []


def test_only_new_entries_count_as_adding_to_a_folder():
    before = [{'type': 'collection', 'id': 'kb-local'}, {'type': 'note', 'id': 'note-1'}]
    assert not folder_sharing.adds_entries(before, before[:1])
    assert folder_sharing.adds_entries(before, [*before, {'type': 'file', 'id': 'file-1'}])
    assert folder_sharing.adds_entries([], ['not an entry'])


@pytest_asyncio.fixture
async def ledger_db():
    from open_webui.models.folder_share_grants import FolderShareGrant
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync: FolderShareGrant.__table__.create(sync))
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        yield db
    await engine.dispose()


@pytest.mark.asyncio
async def test_the_ledger_records_each_principal_once_and_forgets_only_the_named_ones(ledger_db):
    from open_webui.models.folder_share_grants import FolderShareGrants

    await FolderShareGrants.record('dana', 'knowledge', 'kb-local', {('user', 'frank')}, db=ledger_db)
    await FolderShareGrants.record(
        'dana', 'knowledge', 'kb-local', {('user', 'frank'), ('group', 'team')}, db=ledger_db
    )
    await FolderShareGrants.forget('knowledge', 'kb-local', {('user', 'frank')}, db=ledger_db)
    rows = await FolderShareGrants.get_by_owner('dana', db=ledger_db)
    assert [(row.principal_type, row.principal_id) for row in rows] == [('group', 'team')]


def _route(monkeypatch, path, folder, method='POST'):
    from open_webui.routers import folders
    from open_webui.utils.auth import get_verified_user

    async def nothing(*args, **kwargs):
        return None

    async def found(*args, **kwargs):
        return folder

    async def write_access(*args, **kwargs):
        return True

    monkeypatch.setattr(folders, 'configured', lambda: True)
    monkeypatch.setattr(folders, 'check_folders_permission', nothing)
    monkeypatch.setattr(folders.Folders, 'get_folder_by_id', found)
    monkeypatch.setattr(folders.Folders, 'get_folder_by_id_and_user_id', nothing)
    monkeypatch.setattr(folders.Users, 'get_user_by_id', found)
    monkeypatch.setattr(folders, '_has_folder_access', write_access)
    monkeypatch.setattr(folders, 'can_read_all_folder_files', write_access)
    app = FastAPI()
    endpoint = next(route.endpoint for route in folders.router.routes if route.path == path and method in route.methods)
    app.add_api_route(path, endpoint, methods=[method], response_model=None)
    member = SimpleNamespace(id='frank', role='user', email='frank@example.invalid', name='Frank')
    app.dependency_overrides[get_verified_user] = lambda: member
    return TestClient(app, raise_server_exceptions=False)


def test_a_write_member_cannot_change_who_a_folder_is_shared_with(monkeypatch):
    folder = SimpleNamespace(id='project', user_id='dana', parent_id=None, data={})
    client = _route(monkeypatch, '/{id}/access/update', folder)
    response = client.post('/project/access/update', json={'access_grants': [grant('user', 'gwen')]})
    assert response.status_code == 403


def test_a_write_member_cannot_add_knowledge_to_a_shared_folder(monkeypatch):
    folder = SimpleNamespace(
        id='project', user_id='dana', parent_id=None, data={'files': [{'type': 'collection', 'id': 'kb-local'}]}
    )
    client = _route(monkeypatch, '/{id}/update', folder)
    added = {'data': {'files': [{'type': 'collection', 'id': 'kb-local'}, {'type': 'collection', 'id': 'kb-x'}]}}
    response = client.post('/project/update', json=added)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_the_share_dialog_lists_cloud_knowledge_and_other_peoples_items_as_left_out(world, monkeypatch):
    async def file_by_id(file_id, db=None):
        return SimpleNamespace(id=file_id, user_id='erik', filename='offerte.pdf')

    monkeypatch.setattr(folder_sharing.Files, 'get_file_by_id', file_by_id)
    left_out = await folder_sharing.left_out('dana', world.folders['project'].data['files'])
    assert left_out == [
        {'type': 'collection', 'id': 'kb-cloud', 'name': 'OneDrive Projecten', 'reason': 'cloud'},
        {'type': 'collection', 'id': 'kb-other', 'name': 'Contracten', 'reason': 'owner'},
        {'type': 'note', 'id': 'note-erik', 'name': 'Notulen', 'reason': 'owner'},
        {'type': 'file', 'id': 'file-1', 'name': 'offerte.pdf', 'reason': 'owner'},
    ]


def test_a_write_member_sees_the_share_dialog_read_only_and_not_what_it_leaves_out(monkeypatch):
    folder = SimpleNamespace(
        id='project', user_id='dana', parent_id=None, data={'files': [{'type': 'collection', 'id': 'kb-cloud'}]}
    )
    client = _route(monkeypatch, '/{id}/sharing', folder, method='GET')
    response = client.get('/project/sharing')
    assert response.status_code == 200
    assert response.json() == {'shares_knowledge': True, 'may_share': False, 'left_out': []}


def test_someone_who_cannot_read_the_folder_gets_no_sharing_view(monkeypatch):
    from open_webui.routers import folders

    async def denied(*args, **kwargs):
        return False

    folder = SimpleNamespace(id='project', user_id='dana', parent_id=None, data={})
    client = _route(monkeypatch, '/{id}/sharing', folder, method='GET')
    monkeypatch.setattr(folders, '_has_folder_access', denied)
    assert client.get('/project/sharing').status_code == 404


def test_without_soev_api_a_write_member_still_may_not_share(monkeypatch):
    """Upstream v0.12.3: editing a folder's contents does not grant managing who it is shared with."""
    from open_webui.routers import folders

    folder = SimpleNamespace(id='project', user_id='dana', parent_id=None, data={})
    client = _route(monkeypatch, '/{id}/sharing', folder, method='GET')
    monkeypatch.setattr(folders, 'configured', lambda: False)
    assert client.get('/project/sharing').json() == {'shares_knowledge': False, 'may_share': False, 'left_out': []}
