from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from open_webui.models.models import ModelMeta
from open_webui.soev import acting, assistant_sharing, knowledge_sharing
from open_webui.test.soev.test_folder_sharing import grant, model, readers, share
from open_webui.test.soev.test_folder_sharing import world as world


class AssistantLedger:
    def __init__(self):
        self.rows = set()

    async def get_by_owner(self, owner_id):
        return [
            SimpleNamespace(assistant_id=a, resource_type=t, resource_id=r, principal_type=p, principal_id=i)
            for o, a, t, r, p, i in self.rows
            if o == owner_id
        ]

    async def record(self, owner_id, assistant_id, resource_type, resource_id, principals):
        self.rows |= {(owner_id, assistant_id, resource_type, resource_id, *p) for p in principals}

    async def forget(self, assistant_id, resource_type, resource_id, principals):
        self.rows = {
            row
            for row in self.rows
            if not (row[1:4] == (assistant_id, resource_type, resource_id) and row[4:] in principals)
        }


@pytest.fixture
def assistants(world, monkeypatch):
    world.assistants = {}
    world.assistant_ledger = AssistantLedger()
    monkeypatch.setattr(knowledge_sharing, 'AssistantShareGrants', world.assistant_ledger)
    monkeypatch.setattr(assistant_sharing, 'configured', lambda: True)
    monkeypatch.setattr(assistant_sharing.assistant_files, 'narrow', AsyncMock())
    monkeypatch.setattr(knowledge_sharing, 'assistants_view', AsyncMock(side_effect=lambda _: world.assistants.copy()))
    return world


def assistant(world, assistant_id='helper', grants=None, entries=None):
    return SimpleNamespace(
        id=assistant_id,
        user_id='dana',
        meta=ModelMeta(knowledge=entries if entries is not None else world.folders['project'].data['files']),
        access_grants=[model('model', assistant_id, row) for row in grants or []],
    )


async def save(world, planned, assistant_id='helper'):
    async with assistant_sharing.changing('dana', assistant_id, planned):
        if planned is None:
            world.assistants.pop(assistant_id, None)
        else:
            world.assistants[assistant_id] = planned


@pytest.mark.asyncio
async def test_assistant_shares_only_owners_local_knowledge_and_notes(assistants):
    await save(assistants, assistant(assistants, grants=[grant('user', 'frank', 'write'), grant('group', 'team')]))
    assert readers(assistants, 'knowledge', 'kb-local') == {('user', 'frank', 'read'), ('group', 'team', 'read')}
    assert readers(assistants, 'note', 'note-1') == {('user', 'frank', 'read'), ('group', 'team', 'read')}
    assert {ref for *_, ref in assistants.writes} == {'owui:user:dana'}
    assert {(t, r) for t, r, _ in assistants.writes} == {('knowledge', 'kb-local'), ('note', 'note-1')}
    assert assistants.assistant_ledger.rows


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['unshare', 'remove', 'delete'])
async def test_assistant_revokes_only_its_recorded_grants(assistants, change):
    assistants.grants[('knowledge', 'kb-local')] = [grant('user', 'gwen')]
    await save(assistants, assistant(assistants, grants=[grant('user', 'frank'), grant('user', 'gwen')]))
    planned = (
        None
        if change == 'delete'
        else assistant(
            assistants,
            grants=[] if change == 'unshare' else [grant('user', 'frank')],
            entries=[] if change == 'remove' else None,
        )
    )
    await save(assistants, planned)
    assert readers(assistants, 'knowledge', 'kb-local') == {('user', 'gwen', 'read')}
    assert readers(assistants, 'note', 'note-1') == set()
    assert not assistants.assistant_ledger.rows


@pytest.mark.asyncio
@pytest.mark.parametrize('first', ['folder', 'assistant'])
@pytest.mark.parametrize('unshare_first', ['folder', 'assistant'])
async def test_folder_and_assistant_union_survives_until_both_unshare(assistants, first, unshare_first):
    async def folder(on):
        await share(assistants, 'project', [grant('user', 'frank')] if on else [])

    async def model_share(on):
        await save(assistants, assistant(assistants, grants=[grant('user', 'frank')] if on else []))

    operations = {'folder': folder, 'assistant': model_share}
    for kind in (first, 'assistant' if first == 'folder' else 'folder'):
        await operations[kind](True)
    await operations[unshare_first](False)
    assert readers(assistants, 'knowledge', 'kb-local') == {('user', 'frank', 'read')}
    await operations['assistant' if unshare_first == 'folder' else 'folder'](False)
    assert readers(assistants, 'knowledge', 'kb-local') == set()
    assert not assistants.ledger.rows and not assistants.assistant_ledger.rows


@pytest.mark.asyncio
async def test_two_assistants_keep_the_grant_until_both_are_deleted(assistants):
    for name in ('helper', 'guide'):
        await save(assistants, assistant(assistants, assistant_id=name, grants=[grant('user', 'frank')]), name)
    await save(assistants, None)
    assert readers(assistants, 'note', 'note-1') == {('user', 'frank', 'read')}
    await save(assistants, None, 'guide')
    assert readers(assistants, 'note', 'note-1') == set()


@pytest.mark.asyncio
async def test_narrowing_failure_refuses_assistant_commit(assistants):
    from open_webui.soev.client import SoevApiError

    await save(assistants, assistant(assistants, grants=[grant('user', 'frank')]))
    assistants.fail_writes = True
    with pytest.raises(SoevApiError):
        await save(assistants, None)
    assert 'helper' in assistants.assistants


@pytest.mark.asyncio
async def test_another_requester_cannot_reconcile_an_assistant(assistants, monkeypatch):
    monkeypatch.setattr(acting, 'requester_ref', lambda: 'owui:user:frank')
    with pytest.raises(PermissionError):
        await save(assistants, assistant(assistants))
    assert not assistants.writes
