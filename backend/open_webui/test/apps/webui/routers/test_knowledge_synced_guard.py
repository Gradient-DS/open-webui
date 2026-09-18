"""Synced knowledge bases reject grant changes and destructive workspace actions."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from open_webui.models.access_grants import AccessGrantModel
from open_webui.models.knowledge import KnowledgeModel, is_synced_kb
from open_webui.routers import knowledge
from open_webui.soev.knowledge_store import SoevKnowledgeTable
from open_webui.utils.features import FEATURE_FLAGS

GRANTS = [
    {'principal_type': 'user', 'principal_id': 'reader', 'permission': 'read'},
    {'principal_type': 'group', 'principal_id': 'editors', 'permission': 'write'},
]


@pytest.fixture
def api(monkeypatch):
    """Exercise HTTP handlers with isolated knowledge and grant persistence."""
    kb = KnowledgeModel(
        id='kb', user_id='owner', name='Research', description='Notes', type='onedrive', created_at=1, updated_at=1
    )
    user = SimpleNamespace(id='owner', role='user')
    writes = SimpleNamespace(
        update=AsyncMock(return_value=kb),
        grants=AsyncMock(side_effect=lambda *args, **kwargs: kb.access_grants),
        delete=AsyncMock(return_value=True),
        reset=AsyncMock(return_value=kb),
        filter=AsyncMock(side_effect=lambda permissions, user_id, role, grants, feature: grants),
    )
    monkeypatch.setattr(knowledge.Knowledges, 'get_knowledge_by_id', AsyncMock(return_value=kb))
    monkeypatch.setattr(knowledge.Knowledges, 'update_knowledge_by_id', writes.update)
    monkeypatch.setattr(knowledge.Knowledges, 'update_knowledge_meta_by_id', AsyncMock(return_value=kb))
    monkeypatch.setattr(knowledge.Knowledges, 'get_file_metadatas_by_id', AsyncMock(return_value=[]))
    monkeypatch.setattr(knowledge.Knowledges, 'soft_delete_by_id', writes.delete)
    monkeypatch.setattr(knowledge.Knowledges, 'reset_knowledge_by_id', writes.reset)
    monkeypatch.setattr(knowledge.AccessGrants, 'set_access_grants', writes.grants)
    monkeypatch.setattr(knowledge, 'filter_allowed_access_grants', writes.filter)
    monkeypatch.setattr(knowledge, 'embed_knowledge_base_metadata', AsyncMock())
    monkeypatch.setattr(knowledge, 'publish_event', AsyncMock())
    monkeypatch.setattr(knowledge.Config, 'get', AsyncMock(return_value={}))
    monkeypatch.setattr(knowledge, '_get_external_connections', AsyncMock(return_value=[{'id': 'external'}]))
    monkeypatch.setattr(knowledge, '_set_external_connections', AsyncMock())
    monkeypatch.setattr(knowledge, '_get_external_source_test_result', AsyncMock(return_value={'documents': [{}]}))
    monkeypatch.setitem(FEATURE_FLAGS, 'knowledge', True)
    app = FastAPI()
    app.include_router(knowledge.router, prefix='/knowledge')
    app.dependency_overrides[knowledge.get_verified_user] = lambda: user
    app.dependency_overrides[knowledge.get_admin_user] = lambda: user
    app.dependency_overrides[knowledge.get_async_session] = lambda: None
    with TestClient(app) as browser:
        yield SimpleNamespace(kb=kb, user=user, writes=writes, browser=browser)


def set_grants(api, grants):
    api.kb.access_grants = [
        AccessGrantModel(id=str(index), resource_type='knowledge', resource_id='kb', created_at=1, **grant)
        for index, grant in enumerate(grants)
    ]


def update(api, route, grants):
    body = {'name': 'Research', 'description': 'Notes', 'access_grants': grants}
    if route == 'external':
        api.kb.meta = {'source': 'external', 'external': {'connection_id': 'external'}}
        body.update(
            connection={'name': 'Remote', 'provider': 'qdrant', 'endpoint': 'https://external.invalid'},
            source={'name': 'documents', 'config': {'content_field': 'text'}},
            test_query='test',
        )
        return api.browser.patch('/knowledge/external/source/kb', json=body)
    if route == 'access/update':
        body = {'access_grants': grants}
    return api.browser.post('/knowledge/kb/' + route, json=body)


@pytest.mark.parametrize('provider', ['onedrive', 'google_drive'])
@pytest.mark.parametrize('route', ['update', 'access/update', 'external'])
@pytest.mark.parametrize('change', ['add', 'remove', 'permission'])
def test_access_grants_change_on_a_synced_kb_is_403(api, provider, route, change):
    """All existing-KB grant routes reject widening, removal and permission changes."""
    api.kb.type = provider
    if route == 'external':
        api.user.role = 'admin'
    set_grants(api, GRANTS if change != 'add' else [])
    requested = [] if change == 'remove' else [GRANTS[0], {**GRANTS[1], 'permission': 'read'}]
    result = update(api, route, requested)
    assert result.status_code == 403
    assert result.json()['detail']['code'] == 'synced_kb_not_shareable'
    api.writes.update.assert_not_awaited()
    api.writes.grants.assert_not_awaited()
    api.writes.filter.assert_not_awaited()


@pytest.mark.parametrize('provider', ['onedrive', 'google_drive'])
@pytest.mark.parametrize('route', ['update', 'access/update', 'external'])
@pytest.mark.parametrize('grants', [[], GRANTS])
def test_same_grants_is_allowed(api, provider, route, grants):
    """Unchanged grants pass through without filtering, irrespective of order and row metadata."""
    api.kb.type = provider
    if route == 'external':
        api.user.role = 'admin'
    set_grants(api, grants)
    requested = list(reversed(grants))
    result = update(api, route, requested)
    assert result.status_code == 200, result.text
    api.writes.filter.assert_not_awaited()
    if route == 'access/update':
        assert api.writes.grants.await_args.args[2] == requested
    else:
        assert api.writes.update.await_args.kwargs['form_data'].access_grants == requested


@pytest.mark.parametrize('provider', ['onedrive', 'google_drive'])
@pytest.mark.parametrize('role', ['user', 'admin'])
@pytest.mark.parametrize('action', ['delete', 'reset'])
def test_delete_and_reset_keep_their_guard(api, provider, role, action):
    """Owners and admins cannot delete or reset a KB projected from a cloud subscription."""
    api.kb.type = provider
    api.user.role = role
    path = '/knowledge/kb/' + action
    result = api.browser.delete(path) if action == 'delete' else api.browser.post(path)
    assert result.status_code == 403
    assert result.json()['detail'] == 'This knowledge base is synced from a cloud source.'
    api.writes.delete.assert_not_awaited()
    api.writes.reset.assert_not_awaited()


@pytest.mark.parametrize('provider', ['onedrive', 'google_drive'])
@pytest.mark.parametrize('include_null', [False, True])
def test_metadata_update_without_grants_is_allowed(api, provider, include_null):
    """An omitted or null grant field leaves existing synced grants untouched."""
    api.kb.type = provider
    set_grants(api, GRANTS)
    body = {'name': 'Renamed', 'description': 'Notes'}
    if include_null:
        body['access_grants'] = None
    result = api.browser.post('/knowledge/kb/update', json=body)
    assert result.status_code == 200
    assert api.writes.update.await_args.kwargs['form_data'].access_grants is None
    api.writes.filter.assert_not_awaited()


@pytest.mark.parametrize('route', ['update', 'access/update'])
def test_local_grants_still_use_permission_filtering(api, route):
    """Local knowledge bases retain their normal grant editing and filtering behavior."""
    api.kb.type = 'local'
    result = update(api, route, GRANTS)
    assert result.status_code == 200
    api.writes.filter.assert_awaited_once()


@pytest.mark.parametrize('provider', ['local', 'confluence'])
def test_legacy_confluence_metadata_does_not_mark_a_kb_as_synced(api, provider):
    """The predicate follows cloud type projection rather than legacy Confluence metadata."""
    api.kb.type = provider
    api.kb.meta = {'confluence_sync': {'shared': True}}
    assert not is_synced_kb(api.kb)


@pytest.mark.parametrize('action', ['update', 'access/update', 'external', 'delete', 'reset'])
def test_a_co_writer_who_did_not_register_the_source_still_hits_the_guard(api, monkeypatch, action):
    """Collection subscriptions enforce every guard for a co-writer with no viewer-owned schedules."""
    api.user.id = 'co-writer'
    if action == 'external':
        api.user.role = 'admin'
    row = {
        'key': 'kb',
        'name': 'Research',
        'description': 'Notes',
        'created_by': 'owui:user:owner',
        'visibility': 'restricted',
        'principals': ['owui:user:owner', 'owui:user:co-writer'],
        'writers': ['owui:user:owner', 'owui:user:co-writer'],
        'subscriptions': ['onedrive'],
        'created_at': '2026-01-01T00:00:00Z',
        'updated_at': '2026-01-01T00:00:00Z',
    }
    store = SoevKnowledgeTable(service_principal='owui:service:webui')
    monkeypatch.setattr(store, '_collection', AsyncMock(return_value=row))
    schedules = AsyncMock(return_value=[])
    monkeypatch.setattr(store, '_pages', schedules)
    monkeypatch.setattr(knowledge.Knowledges, 'get_knowledge_by_id', store.get_knowledge_by_id)
    if action == 'external':

        async def external_knowledge(*args, **kwargs):
            projected = await store.get_knowledge_by_id(*args, **kwargs)
            projected.meta = api.kb.meta
            return projected

        monkeypatch.setattr(knowledge.Knowledges, 'get_knowledge_by_id', external_knowledge)
    monkeypatch.setattr(knowledge.AccessGrants, 'has_access', AsyncMock(return_value=True))
    if action == 'delete':
        result = api.browser.delete('/knowledge/kb/delete')
    elif action == 'reset':
        result = api.browser.post('/knowledge/kb/reset')
    else:
        result = update(api, action, GRANTS)
    assert result.status_code == 403
    if action in ('delete', 'reset'):
        assert result.json()['detail'] == 'This knowledge base is synced from a cloud source.'
    else:
        assert result.json()['detail']['code'] == 'synced_kb_not_shareable'
    schedules.assert_not_awaited()
    api.writes.update.assert_not_awaited()
    api.writes.grants.assert_not_awaited()
    api.writes.delete.assert_not_awaited()
    api.writes.reset.assert_not_awaited()
