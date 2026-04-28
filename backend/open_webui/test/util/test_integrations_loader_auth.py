"""Router-level test for the loader-bearer path on ``/api/v1/integrations/ingest``.

Verifies that when a call presents ``LOADER_INGEST_API_KEY`` plus the acting
headers, the ``user_id`` propagated to ``_process_*_document`` (and therefore
to ``Files.insert_new_file``) equals the ``X-Acting-User-Id`` value — not a
service account.

We mount the router on a throwaway app, override the auth dependency to inject
a real ``LoaderPrincipal``, and mock the heavy downstream collaborators so the
test exercises only the wiring this plan actually changed.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from open_webui.routers import integrations as integrations_router
from open_webui.utils.service_auth import LoaderPrincipal, get_integration_principal


@pytest.fixture
def acting_user_id() -> str:
    return 'lex-uuid-42'


@pytest.fixture
def loader_principal(acting_user_id):
    user = MagicMock()
    user.id = acting_user_id
    user.email = 'lex@gradient-ds.com'
    user.role = 'user'
    user.name = 'Lex'
    user.info = {'integration_provider': 'should-be-ignored'}
    return LoaderPrincipal(user=user, provider_slug='onedrive')


@pytest.fixture
def app(loader_principal):
    """FastAPI app with the integrations router and the auth dep overridden."""
    app = FastAPI()
    app.include_router(integrations_router.router, prefix='/api/v1/integrations')

    # Provide the minimum app.state.config that ingest_documents reads.
    app.state.config = SimpleNamespace(
        INTEGRATION_PROVIDERS={
            'onedrive': {
                'max_documents_per_request': 50,
                'max_files_per_kb': 1000,
                'custom_metadata_fields': [],
            }
        },
    )

    # Inject the LoaderPrincipal directly — bypass the bearer/header check
    # (covered by test_service_auth.py) so this test focuses on user_id flow.
    app.dependency_overrides[get_integration_principal] = lambda: loader_principal

    return app


def test_ingest_with_loader_bearer_attributes_files_to_acting_user(app, loader_principal, acting_user_id):
    fake_kb = MagicMock()
    fake_kb.id = 'kb-uuid-1'
    fake_kb.meta = {'integration': {'data_type': 'chunked_text'}}
    fake_kb.name = 'Test KB'
    fake_kb.description = ''
    fake_kb.type = 'onedrive'

    captured = {}

    def fake_process_chunked_text_document(*, request, knowledge_id, provider, doc, user_id):
        captured.setdefault('calls', []).append(
            {'knowledge_id': knowledge_id, 'provider': provider, 'user_id': user_id, 'source_id': doc.source_id}
        )
        return {'source_id': doc.source_id, 'file_id': f'{provider}-{doc.source_id}', 'status': 'created'}

    payload = {
        'collection': {
            'source_id': 'onedrive-drive-1',
            'name': 'Test KB',
            'data_type': 'chunked_text',
        },
        'documents': [
            {
                'source_id': 'doc-A',
                'filename': 'a.txt',
                'content_type': 'text/plain',
                'chunks': ['hello', 'world'],
            },
            {
                'source_id': 'doc-B',
                'filename': 'b.txt',
                'content_type': 'text/plain',
                'chunks': ['foo'],
            },
        ],
    }

    with (
        patch.object(integrations_router, '_find_kb_by_source_id', return_value=fake_kb),
        patch.object(
            integrations_router, '_process_chunked_text_document', side_effect=fake_process_chunked_text_document
        ),
        patch.object(integrations_router.Knowledges, 'get_files_by_id', return_value=[]),
        patch.object(integrations_router.Knowledges, 'update_knowledge_by_id', return_value=fake_kb),
    ):
        client = TestClient(app)
        resp = client.post(
            '/api/v1/integrations/ingest',
            data={'data': json.dumps(payload)},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body['knowledge_id'] == 'kb-uuid-1'
    assert body['provider'] == 'onedrive'
    assert body['data_type'] == 'chunked_text'
    assert body['total'] == 2
    assert body['created'] == 2
    assert body['errors'] == 0

    # The critical assertion: every call to the processor used the acting user's id.
    assert len(captured.get('calls', [])) == 2
    for call in captured['calls']:
        assert call['user_id'] == acting_user_id, (
            f'Expected user_id={acting_user_id} (X-Acting-User-Id), got {call["user_id"]}'
        )
        assert call['provider'] == 'onedrive'  # from LoaderPrincipal.provider_slug, not user.info


def test_ingest_with_loader_bearer_unknown_provider_returns_403(app, loader_principal):
    """If the LoaderPrincipal's provider_slug isn't in INTEGRATION_PROVIDERS, reject."""
    # Override the principal to use an unregistered provider.
    app.dependency_overrides[get_integration_principal] = lambda: LoaderPrincipal(
        user=loader_principal.user,
        provider_slug='unregistered-provider',
    )

    payload = {
        'collection': {
            'source_id': 'x',
            'name': 'Y',
            'data_type': 'chunked_text',
        },
        'documents': [],
    }
    client = TestClient(app)
    resp = client.post('/api/v1/integrations/ingest', data={'data': json.dumps(payload)})
    assert resp.status_code == 403
    assert 'unregistered-provider' in resp.json()['detail']
