"""Router-level test for ``/api/v1/internal/retrieval/query``.

The endpoint is a thin shim over :func:`run_agent_search`; this test focuses
on the wiring this plan introduced — auth dependency override, feature flag
gating, request-validation envelope, and the response shape.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from open_webui.routers import internal_retrieval as internal_retrieval_router
from open_webui.utils.service_auth import AgentPrincipal, get_agent_principal


@pytest.fixture
def fake_principal():
    user = MagicMock()
    user.id = 'user-uuid-1'
    user.email = 'lex@gradient-ds.com'
    user.role = 'user'
    user.name = 'Lex'
    return AgentPrincipal(agent_id='langgraph_dev', user=user)


def _build_app(*, fake_principal, agent_search_enabled=True):
    app = FastAPI()
    app.include_router(internal_retrieval_router.router, prefix='/api/v1/internal/retrieval')
    app.state.config = SimpleNamespace(AGENT_SEARCH_ENABLED=agent_search_enabled)
    app.dependency_overrides[get_agent_principal] = lambda: fake_principal
    return app


def test_query_returns_results_when_enabled(monkeypatch, fake_principal):
    app = _build_app(fake_principal=fake_principal)

    captured = {}

    async def fake_run_agent_search(*, request, user, query, top_k, kb_ids):
        captured['user_id'] = user.id
        captured['query'] = query
        captured['top_k'] = top_k
        captured['kb_ids'] = kb_ids
        return [
            {
                'kb_id': 'kb-shared',
                'file_id': 'file-1',
                'chunk': 'hello world',
                'score': 0.9,
                'metadata': {'file_id': 'file-1', 'name': 'hello.md'},
            }
        ]

    monkeypatch.setattr(internal_retrieval_router, 'run_agent_search', fake_run_agent_search)

    client = TestClient(app)
    resp = client.post(
        '/api/v1/internal/retrieval/query',
        json={'query': 'hello', 'top_k': 5, 'kb_ids': ['kb-shared']},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        'results': [
            {
                'kb_id': 'kb-shared',
                'file_id': 'file-1',
                'chunk': 'hello world',
                'score': 0.9,
                'metadata': {'file_id': 'file-1', 'name': 'hello.md'},
            }
        ]
    }
    assert captured == {
        'user_id': 'user-uuid-1',
        'query': 'hello',
        'top_k': 5,
        'kb_ids': ['kb-shared'],
    }


def test_query_returns_404_when_feature_flag_disabled(monkeypatch, fake_principal):
    app = _build_app(fake_principal=fake_principal, agent_search_enabled=False)

    monkeypatch.setattr(
        internal_retrieval_router,
        'run_agent_search',
        AsyncMock(side_effect=AssertionError('should not run search when disabled')),
    )

    client = TestClient(app)
    resp = client.post('/api/v1/internal/retrieval/query', json={'query': 'hello'})
    assert resp.status_code == 404


def test_query_validates_request_body(fake_principal):
    app = _build_app(fake_principal=fake_principal)
    client = TestClient(app)

    # Empty query is rejected by Field(min_length=1).
    resp = client.post('/api/v1/internal/retrieval/query', json={'query': ''})
    assert resp.status_code == 422

    # top_k out of range.
    resp = client.post(
        '/api/v1/internal/retrieval/query',
        json={'query': 'hi', 'top_k': 0},
    )
    assert resp.status_code == 422

    resp = client.post(
        '/api/v1/internal/retrieval/query',
        json={'query': 'hi', 'top_k': 1000},
    )
    assert resp.status_code == 422


def test_query_defaults_top_k_when_omitted(monkeypatch, fake_principal):
    app = _build_app(fake_principal=fake_principal)
    seen = {}

    async def fake_run_agent_search(*, request, user, query, top_k, kb_ids):
        seen['top_k'] = top_k
        seen['kb_ids'] = kb_ids
        return []

    monkeypatch.setattr(internal_retrieval_router, 'run_agent_search', fake_run_agent_search)

    client = TestClient(app)
    resp = client.post('/api/v1/internal/retrieval/query', json={'query': 'hi'})
    assert resp.status_code == 200
    assert seen == {'top_k': 10, 'kb_ids': None}


# ---------- /accessible-kbs ----------------------------------------------------------


def test_accessible_kbs_returns_payload_when_enabled(monkeypatch, fake_principal):
    app = _build_app(fake_principal=fake_principal)

    captured = {}

    def fake_resolve(user, *, kb_ids=None):
        captured['user_id'] = user.id
        captured['kb_ids'] = kb_ids
        return {
            'user_id': user.id,
            'kbs': [
                {
                    'id': 'kb-shared',
                    'collection_name': 'KbShared',
                    'name': 'Shared KB',
                    'description': 'Common docs',
                    'type': 'local',
                    'owner_id': 'admin-1',
                }
            ],
            'kb_index_collection_name': 'Knowledge_bases',
        }

    monkeypatch.setattr(internal_retrieval_router, 'resolve_accessible_kbs', fake_resolve)

    client = TestClient(app)
    resp = client.get('/api/v1/internal/retrieval/accessible-kbs')

    assert resp.status_code == 200
    body = resp.json()
    assert body['user_id'] == fake_principal.user.id
    assert body['kb_index_collection_name'] == 'Knowledge_bases'
    assert body['kbs'] == [
        {
            'id': 'kb-shared',
            'collection_name': 'KbShared',
            'name': 'Shared KB',
            'description': 'Common docs',
            'type': 'local',
            'owner_id': 'admin-1',
        }
    ]
    assert captured == {'user_id': fake_principal.user.id, 'kb_ids': None}


def test_accessible_kbs_returns_404_when_disabled(monkeypatch, fake_principal):
    app = _build_app(fake_principal=fake_principal, agent_search_enabled=False)
    monkeypatch.setattr(
        internal_retrieval_router,
        'resolve_accessible_kbs',
        MagicMock(side_effect=AssertionError('should not run when disabled')),
    )
    client = TestClient(app)
    resp = client.get('/api/v1/internal/retrieval/accessible-kbs')
    assert resp.status_code == 404


def test_accessible_kbs_passes_kb_ids_subset(monkeypatch, fake_principal):
    app = _build_app(fake_principal=fake_principal)
    seen = {}

    def fake_resolve(user, *, kb_ids=None):
        seen['kb_ids'] = kb_ids
        return {'user_id': user.id, 'kbs': [], 'kb_index_collection_name': 'Knowledge_bases'}

    monkeypatch.setattr(internal_retrieval_router, 'resolve_accessible_kbs', fake_resolve)

    client = TestClient(app)
    resp = client.get('/api/v1/internal/retrieval/accessible-kbs', params={'kb_ids': 'a,b , ,c'})

    assert resp.status_code == 200
    assert seen['kb_ids'] == ['a', 'b', 'c']


def test_accessible_kbs_no_kb_ids_param(monkeypatch, fake_principal):
    app = _build_app(fake_principal=fake_principal)
    seen = {}

    def fake_resolve(user, *, kb_ids=None):
        seen['kb_ids'] = kb_ids
        return {'user_id': user.id, 'kbs': [], 'kb_index_collection_name': 'Knowledge_bases'}

    monkeypatch.setattr(internal_retrieval_router, 'resolve_accessible_kbs', fake_resolve)

    client = TestClient(app)
    resp = client.get('/api/v1/internal/retrieval/accessible-kbs')
    assert resp.status_code == 200
    assert seen['kb_ids'] is None
