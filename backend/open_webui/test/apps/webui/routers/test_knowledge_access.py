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
