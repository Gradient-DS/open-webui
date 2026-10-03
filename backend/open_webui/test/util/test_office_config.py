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


@pytest.mark.parametrize('office,editing', [(False, False), (False, True), (True, False), (True, True)])
def test_office_runtime_settings_round_trip_through_admin_endpoint(client, office, editing):
    api, values = client
    response = api.get('/code_execution')
    assert response.status_code == 200
    payload = {**response.json(), 'ENABLE_OFFICE': office, 'ENABLE_OFFICE_EDIT': editing}
    updated = api.post('/code_execution', json=payload)
    assert updated.status_code == 200
    assert updated.json()['ENABLE_OFFICE'] is office
    assert updated.json()['ENABLE_OFFICE_EDIT'] is (office and editing)
    assert values['office.enable'] is office
    assert values['office_edit.enable'] is (office and editing)
    assert api.get('/code_execution').json() == updated.json()


def test_older_settings_clients_preserve_new_fields(client):
    api, values = client
    values.update({'office.enable': False, 'office_edit.enable': False, 'document_writer.enable': True})
    payload = api.get('/code_execution').json()
    for key in ['ENABLE_OFFICE', 'ENABLE_OFFICE_EDIT', 'ENABLE_DOCUMENT_WRITER', 'DOCUMENT_WRITER_PROMPT_TEMPLATE']:
        payload.pop(key)
    assert api.post('/code_execution', json=payload).status_code == 200
    assert values['office.enable'] is False
    assert values['office_edit.enable'] is False
    assert values['document_writer.enable'] is True


def test_document_writer_switch_in_the_same_section_round_trips(client):
    api, values = client
    payload = {
        **api.get('/code_execution').json(),
        'ENABLE_DOCUMENT_WRITER': True,
        'DOCUMENT_WRITER_PROMPT_TEMPLATE': 'Write clearly.',
    }
    assert api.post('/code_execution', json=payload).status_code == 200
    assert values['document_writer.enable'] is True
    assert values['document_writer.prompt_template'] == 'Write clearly.'
