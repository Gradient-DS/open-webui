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

    async def fake_resolve(user, *, kb_ids=None):
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
        AsyncMock(side_effect=AssertionError('should not run when disabled')),
    )
    client = TestClient(app)
    resp = client.get('/api/v1/internal/retrieval/accessible-kbs')
    assert resp.status_code == 404


def test_accessible_kbs_passes_kb_ids_subset(monkeypatch, fake_principal):
    app = _build_app(fake_principal=fake_principal)
    seen = {}

    async def fake_resolve(user, *, kb_ids=None):
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

    async def fake_resolve(user, *, kb_ids=None):
        seen['kb_ids'] = kb_ids
        return {'user_id': user.id, 'kbs': [], 'kb_index_collection_name': 'Knowledge_bases'}

    monkeypatch.setattr(internal_retrieval_router, 'resolve_accessible_kbs', fake_resolve)

    client = TestClient(app)
    resp = client.get('/api/v1/internal/retrieval/accessible-kbs')
    assert resp.status_code == 200
    assert seen['kb_ids'] is None


# ---------- /accessible-files -------------------------------------------------------


def _admin_principal():
    user = MagicMock()
    user.id = 'admin-uuid-1'
    user.email = 'admin@gradient-ds.com'
    user.role = 'admin'
    user.name = 'Admin'
    return AgentPrincipal(agent_id='langgraph_dev', user=user)


def test_accessible_files_returns_owned(monkeypatch, fake_principal):
    """Owner of a file gets it back."""
    app = _build_app(fake_principal=fake_principal)

    async def fake_has_access(*, file_id, access_type, user):
        return file_id == 'file-owned' and user.id == fake_principal.user.id

    monkeypatch.setattr(internal_retrieval_router, 'has_access_to_file', fake_has_access)

    client = TestClient(app)
    resp = client.get(
        '/api/v1/internal/retrieval/accessible-files',
        params={'file_ids': 'file-owned'},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {'user_id': fake_principal.user.id, 'file_ids': ['file-owned']}


def test_accessible_files_filters_inaccessible(monkeypatch, fake_principal):
    """A file the user has no access to is dropped from the response."""
    app = _build_app(fake_principal=fake_principal)

    monkeypatch.setattr(
        internal_retrieval_router,
        'has_access_to_file',
        AsyncMock(return_value=False),
    )

    client = TestClient(app)
    resp = client.get(
        '/api/v1/internal/retrieval/accessible-files',
        params={'file_ids': 'file-private'},
    )
    assert resp.status_code == 200
    assert resp.json()['file_ids'] == []


def test_accessible_files_admin_does_not_bypass(monkeypatch):
    """Admin role does NOT grant access to files they don't own/have grants on."""
    admin_principal = _admin_principal()
    app = _build_app(fake_principal=admin_principal)

    # has_access_to_file is the only check — no admin shortcut.
    monkeypatch.setattr(
        internal_retrieval_router,
        'has_access_to_file',
        AsyncMock(return_value=False),
    )

    client = TestClient(app)
    resp = client.get(
        '/api/v1/internal/retrieval/accessible-files',
        params={'file_ids': 'file-private-other-user'},
    )
    assert resp.status_code == 200
    assert resp.json()['file_ids'] == []


def test_accessible_files_mixed_subset(monkeypatch, fake_principal):
    """Returns only the owned/accessible subset, drops the rest."""
    app = _build_app(fake_principal=fake_principal)

    accessible = {'file-owned', 'file-granted'}

    async def fake_has_access(*, file_id, access_type, user):
        return file_id in accessible

    monkeypatch.setattr(internal_retrieval_router, 'has_access_to_file', fake_has_access)

    client = TestClient(app)
    resp = client.get(
        '/api/v1/internal/retrieval/accessible-files',
        params={'file_ids': 'file-owned,file-private,file-granted'},
    )
    assert resp.status_code == 200
    assert sorted(resp.json()['file_ids']) == ['file-granted', 'file-owned']


def test_accessible_files_requires_query_param(fake_principal):
    """Empty file_ids → 400."""
    app = _build_app(fake_principal=fake_principal)
    client = TestClient(app)
    resp = client.get(
        '/api/v1/internal/retrieval/accessible-files',
        params={'file_ids': ''},
    )
    assert resp.status_code == 400


def test_accessible_files_requires_agent_search_enabled(monkeypatch, fake_principal):
    """Feature-flag gate matches /accessible-kbs behavior."""
    app = _build_app(fake_principal=fake_principal, agent_search_enabled=False)

    monkeypatch.setattr(
        internal_retrieval_router,
        'has_access_to_file',
        AsyncMock(side_effect=AssertionError('should not run when disabled')),
    )

    client = TestClient(app)
    resp = client.get(
        '/api/v1/internal/retrieval/accessible-files',
        params={'file_ids': 'file-anything'},
    )
    assert resp.status_code == 404


# ---------- /files/{id}/content tightening ------------------------------------------


def test_files_id_content_admin_no_longer_shortcut(monkeypatch):
    """Admin role does not grant /files/{id}/content access on files the admin
    doesn't own and has no grant on."""
    admin_principal = _admin_principal()
    app = _build_app(fake_principal=admin_principal)

    fake_file = SimpleNamespace(
        id='file-private-other-user',
        user_id='other-user-uuid',
        filename='secret.pdf',
        data={'content': 'should not be returned'},
    )

    class _FakeFiles:
        @staticmethod
        async def get_file_by_id(file_id):
            return fake_file if file_id == fake_file.id else None

    monkeypatch.setattr(internal_retrieval_router, 'Files', _FakeFiles)
    monkeypatch.setattr(
        internal_retrieval_router,
        'has_access_to_file',
        AsyncMock(return_value=False),
    )

    client = TestClient(app)
    resp = client.get(
        '/api/v1/internal/retrieval/files/file-private-other-user/content',
    )
    assert resp.status_code == 403
