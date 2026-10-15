"""Knowledge rights come from the platform, while sharing belongs to the owner."""

import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from open_webui.routers import knowledge
from open_webui.test.apps.webui.routers.test_knowledge_synced_guard import api as knowledge_api_fixture

api = knowledge_api_fixture


@pytest.mark.parametrize('route', ['', '/search', '/kb'])
@pytest.mark.parametrize('writable', [False, True])
def test_admin_write_access_follows_platform(api, monkeypatch, route, writable):
    api.user.role = 'admin'
    monkeypatch.setattr(knowledge, 'can_bypass_access_control', lambda user: True)
    monkeypatch.setattr(knowledge.Groups, 'get_groups_by_member_id', AsyncMock(return_value=[]))
    monkeypatch.setattr(
        knowledge.Knowledges, 'search_knowledge_bases', AsyncMock(return_value=SimpleNamespace(items=[api.kb], total=1))
    )
    monkeypatch.setattr(knowledge.Knowledges, 'get_file_counts_by_knowledge_ids', AsyncMock(return_value={}))
    monkeypatch.setattr(
        knowledge.AccessGrants, 'get_accessible_resource_ids', AsyncMock(return_value={'kb'} if writable else set())
    )
    monkeypatch.setattr(
        knowledge.AccessGrants,
        'has_access',
        AsyncMock(side_effect=lambda **kwargs: kwargs['permission'] == 'read' or writable),
    )
    result = api.browser.get('/knowledge' + (route or '/'))
    assert result.status_code == 200, result.text
    body = result.json()
    assert (body['items'][0] if 'items' in body else body)['write_access'] is writable


def test_admin_bypass_defaults_off_even_with_legacy_content_setting():
    environment = {**os.environ, 'ENABLE_ADMIN_WORKSPACE_CONTENT_ACCESS': 'True'}
    environment.pop('BYPASS_ADMIN_ACCESS_CONTROL', None)
    result = subprocess.run(
        [
            sys.executable,
            '-c',
            'from open_webui.config import BYPASS_ADMIN_ACCESS_CONTROL; assert BYPASS_ADMIN_ACCESS_CONTROL is False',
        ],
        env=environment,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('route', ['update', 'access/update'])
@pytest.mark.parametrize('role', ['user', 'admin'])
def test_non_owner_cannot_change_grants(api, monkeypatch, route, role):
    api.kb.type = 'local'
    api.user.id, api.user.role = 'writer', role
    monkeypatch.setattr(knowledge.AccessGrants, 'has_access', AsyncMock(return_value=True))
    monkeypatch.setattr(knowledge, 'can_bypass_access_control', lambda user: True)
    body = {'access_grants': [{'principal_type': 'user', 'principal_id': 'reader', 'permission': 'read'}]}
    if route == 'update':
        body.update(name='Revised', description='Notes')
    result = api.browser.post('/knowledge/kb/' + route, json=body)
    assert result.status_code == 403, result.text
    api.writes.update.assert_not_awaited()
    api.writes.grants.assert_not_awaited()


@pytest.mark.parametrize('grants', ['omitted', None, []])
def test_non_owner_writer_can_edit_content_without_an_access_write(api, monkeypatch, grants):
    api.kb.type = 'local'
    api.user.id = 'writer'
    monkeypatch.setattr(knowledge.AccessGrants, 'has_access', AsyncMock(return_value=True))
    body = {'name': 'Revised', 'description': 'Notes'}
    if grants != 'omitted':
        body['access_grants'] = grants
    result = api.browser.post('/knowledge/kb/update', json=body)
    assert result.status_code == 200, result.text
    assert api.writes.update.await_args.kwargs['form_data'].access_grants is None
    api.writes.filter.assert_not_awaited()


@pytest.mark.parametrize('route', ['update', 'access/update'])
def test_owner_can_change_access(api, route):
    api.kb.type = 'local'
    body = {'access_grants': [{'principal_type': 'user', 'principal_id': 'reader', 'permission': 'read'}]}
    if route == 'update':
        body.update(name='Revised', description='Notes')
    result = api.browser.post('/knowledge/kb/' + route, json=body)
    assert result.status_code == 200, result.text
    api.writes.filter.assert_awaited_once()


def test_non_owner_reader_gets_403_for_access_change(api, monkeypatch):
    api.kb.type = 'local'
    api.user.id = 'reader'
    monkeypatch.setattr(knowledge.AccessGrants, 'has_access', AsyncMock(return_value=False))
    result = api.browser.post(
        '/knowledge/kb/update',
        json={
            'name': 'Revised',
            'description': 'Notes',
            'access_grants': [{'principal_type': 'user', 'principal_id': 'reader', 'permission': 'write'}],
        },
    )
    assert result.status_code == 403
    api.writes.update.assert_not_awaited()
