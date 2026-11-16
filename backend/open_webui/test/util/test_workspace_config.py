import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from open_webui.config import DEFAULT_CONFIG

# The config route imports tools, which otherwise connect to a vector database at import time.
with patch.dict(sys.modules, {'open_webui.retrieval.vector.factory': SimpleNamespace(VECTOR_DB_CLIENT=MagicMock())}):
    from open_webui.routers import configs


@pytest.fixture
def client(monkeypatch):
    values = dict(DEFAULT_CONFIG)
    monkeypatch.setattr(
        configs.Config, 'get_many', AsyncMock(side_effect=lambda *keys: {key: values[key] for key in keys})
    )
    monkeypatch.setattr(configs.Config, 'get', AsyncMock(side_effect=lambda key: values[key]))
    monkeypatch.setattr(configs.Config, 'upsert', AsyncMock(side_effect=values.update))
    monkeypatch.setattr(configs, 'publish_event', AsyncMock())
    app = FastAPI()
    app.include_router(configs.router)
    app.dependency_overrides[configs.get_admin_user] = lambda: SimpleNamespace(id='admin', role='admin')
    with TestClient(app) as client:
        yield client, values


@pytest.mark.parametrize('workspace', [False, True])
def test_workspace_runtime_settings_round_trip_through_admin_endpoint(client, workspace):
    api, values = client
    response = api.get('/code_execution')
    assert response.status_code == 200
    payload = {**response.json(), 'ENABLE_WORKSPACE': workspace}
    updated = api.post('/code_execution', json=payload)
    assert updated.status_code == 200
    assert updated.json()['ENABLE_WORKSPACE'] is workspace
    assert values['workspace.enable'] is workspace
    assert api.get('/code_execution').json() == updated.json()


def test_older_settings_clients_preserve_new_fields(client):
    api, values = client
    values.update({'workspace.enable': False, 'document_writer.enable': True})
    payload = api.get('/code_execution').json()
    payload.pop('ENABLE_WORKSPACE')
    assert api.post('/code_execution', json=payload).status_code == 200
    assert values['workspace.enable'] is False
    assert values['document_writer.enable'] is True


def test_document_writer_switch_in_the_same_section_round_trips(client):
    api, values = client
    payload = {
        **api.get('/code_execution').json(),
        'ENABLE_DOCUMENT_WRITER': True,
    }
    assert api.post('/code_execution', json=payload).status_code == 200
    assert values['document_writer.enable'] is True


def test_workspace_is_off_unless_the_tenant_enables_it():
    import os
    import subprocess

    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {'ENABLE_WORKSPACE', 'FEATURE_WORKSPACE', 'USER_PERMISSIONS_FEATURES_WORKSPACE'}
    }
    probe = (
        'from open_webui import config; '
        "print(config.ENABLE_WORKSPACE, config.FEATURE_WORKSPACE, config.DEFAULT_USER_PERMISSIONS['features']['workspace'], "
        "config.DEFAULT_CONFIG['workspace.enable'])"
    )
    result = subprocess.run([sys.executable, '-c', probe], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.split()[-4:] == ['False'] * 4
