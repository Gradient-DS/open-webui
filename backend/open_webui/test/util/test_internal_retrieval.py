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


# ---------- /knowledge/{id}/files ---------------------------------------------------


def _fake_knowledge(*, user_id, knowledge_id='kb-1'):
    return SimpleNamespace(id=knowledge_id, user_id=user_id)


def test_knowledge_files_happy_path_owner(monkeypatch, fake_principal, caplog):
    """Owner of a KB gets the file list back; agent_id is logged."""
    app = _build_app(fake_principal=fake_principal)

    knowledge = _fake_knowledge(user_id=fake_principal.user.id)
    captured = {}

    class _FakeKnowledges:
        @staticmethod
        async def get_knowledge_by_id(*, id, db=None):
            captured['get_knowledge_id'] = id
            return knowledge

        @staticmethod
        async def get_suspension_info(kb_id, db=None):
            captured['suspension_check_id'] = kb_id
            return None

        @staticmethod
        async def search_files_by_id(kb_id, user_id, *, filter, skip, limit, db=None):
            captured['search'] = {
                'kb_id': kb_id,
                'user_id': user_id,
                'filter': dict(filter),
                'skip': skip,
                'limit': limit,
            }
            return {
                'items': [
                    {
                        'id': 'file-a',
                        'filename': 'a.pdf',
                        'user_id': fake_principal.user.id,
                        'created_at': 1,
                        'updated_at': 1,
                        'meta': {'content_type': 'application/pdf'},
                    }
                ],
                'total': 1,
            }

    class _FakeAccessGrants:
        @staticmethod
        async def has_access(*, user_id, resource_type, resource_id, permission, db=None):
            captured['access_check'] = {
                'user_id': user_id,
                'resource_type': resource_type,
                'resource_id': resource_id,
                'permission': permission,
            }
            return False  # not used: owner short-circuits

    monkeypatch.setattr(internal_retrieval_router, 'Knowledges', _FakeKnowledges)
    monkeypatch.setattr(internal_retrieval_router, 'AccessGrants', _FakeAccessGrants)

    with caplog.at_level('INFO', logger=internal_retrieval_router.log.name):
        client = TestClient(app)
        resp = client.get(
            '/api/v1/internal/retrieval/knowledge/kb-1/files',
            params={'query': 'budget', 'limit': 50, 'page': 2},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body['total'] == 1
    assert body['items'][0]['id'] == 'file-a'
    assert captured['get_knowledge_id'] == 'kb-1'
    assert captured['suspension_check_id'] == knowledge.id
    assert captured['search'] == {
        'kb_id': knowledge.id,
        'user_id': fake_principal.user.id,
        'filter': {'query': 'budget'},
        'skip': 50,  # (page=2 - 1) * limit=50
        'limit': 50,
    }
    # Owner short-circuits — has_access never consulted.
    assert 'access_check' not in captured
    assert any(
        'agent_list_knowledge_files' in rec.getMessage() and fake_principal.agent_id in rec.getMessage()
        for rec in caplog.records
    )


def test_knowledge_files_happy_path_grant(monkeypatch, fake_principal):
    """Non-owner with a read grant gets the file list back."""
    app = _build_app(fake_principal=fake_principal)
    knowledge = _fake_knowledge(user_id='someone-else')

    class _FakeKnowledges:
        @staticmethod
        async def get_knowledge_by_id(*, id, db=None):
            return knowledge

        @staticmethod
        async def get_suspension_info(kb_id, db=None):
            return None

        @staticmethod
        async def search_files_by_id(kb_id, user_id, *, filter, skip, limit, db=None):
            return {'items': [], 'total': 0}

    class _FakeAccessGrants:
        @staticmethod
        async def has_access(*, user_id, resource_type, resource_id, permission, db=None):
            return True

    monkeypatch.setattr(internal_retrieval_router, 'Knowledges', _FakeKnowledges)
    monkeypatch.setattr(internal_retrieval_router, 'AccessGrants', _FakeAccessGrants)

    client = TestClient(app)
    resp = client.get('/api/v1/internal/retrieval/knowledge/kb-1/files')
    assert resp.status_code == 200
    assert resp.json() == {'items': [], 'total': 0}


def test_knowledge_files_403_when_no_grant_and_not_owner(monkeypatch, fake_principal):
    """Non-owner without a read grant → 403."""
    app = _build_app(fake_principal=fake_principal)

    class _FakeKnowledges:
        @staticmethod
        async def get_knowledge_by_id(*, id, db=None):
            return _fake_knowledge(user_id='someone-else')

        @staticmethod
        async def get_suspension_info(kb_id, db=None):
            raise AssertionError('should not check suspension when ACL fails')

        @staticmethod
        async def search_files_by_id(*args, **kwargs):
            raise AssertionError('should not search when ACL fails')

    class _FakeAccessGrants:
        @staticmethod
        async def has_access(*, user_id, resource_type, resource_id, permission, db=None):
            return False

    monkeypatch.setattr(internal_retrieval_router, 'Knowledges', _FakeKnowledges)
    monkeypatch.setattr(internal_retrieval_router, 'AccessGrants', _FakeAccessGrants)

    client = TestClient(app)
    resp = client.get('/api/v1/internal/retrieval/knowledge/kb-1/files')
    assert resp.status_code == 403


def test_knowledge_files_403_when_admin_role_but_no_grant(monkeypatch):
    """Admin role does NOT bypass the agent-side ACL (mirrors /accessible-files)."""
    admin_principal = _admin_principal()
    app = _build_app(fake_principal=admin_principal)

    class _FakeKnowledges:
        @staticmethod
        async def get_knowledge_by_id(*, id, db=None):
            return _fake_knowledge(user_id='someone-else')

        @staticmethod
        async def get_suspension_info(kb_id, db=None):
            return None

        @staticmethod
        async def search_files_by_id(*args, **kwargs):
            raise AssertionError('admin must not bypass agent ACL')

    class _FakeAccessGrants:
        @staticmethod
        async def has_access(*, user_id, resource_type, resource_id, permission, db=None):
            return False

    monkeypatch.setattr(internal_retrieval_router, 'Knowledges', _FakeKnowledges)
    monkeypatch.setattr(internal_retrieval_router, 'AccessGrants', _FakeAccessGrants)

    client = TestClient(app)
    resp = client.get('/api/v1/internal/retrieval/knowledge/kb-1/files')
    assert resp.status_code == 403


def test_knowledge_files_403_when_suspended(monkeypatch, fake_principal):
    """Suspended KB → 403 even for the owner (no admin bypass on this surface)."""
    app = _build_app(fake_principal=fake_principal)
    knowledge = _fake_knowledge(user_id=fake_principal.user.id)

    class _FakeKnowledges:
        @staticmethod
        async def get_knowledge_by_id(*, id, db=None):
            return knowledge

        @staticmethod
        async def get_suspension_info(kb_id, db=None):
            return {'days_remaining': 7}

        @staticmethod
        async def search_files_by_id(*args, **kwargs):
            raise AssertionError('suspended KB must not be queried')

    monkeypatch.setattr(internal_retrieval_router, 'Knowledges', _FakeKnowledges)
    monkeypatch.setattr(
        internal_retrieval_router,
        'AccessGrants',
        SimpleNamespace(has_access=AsyncMock(return_value=True)),
    )

    client = TestClient(app)
    resp = client.get('/api/v1/internal/retrieval/knowledge/kb-1/files')
    assert resp.status_code == 403
    assert '7 days' in resp.json()['detail']


def test_knowledge_files_404_when_feature_flag_disabled(monkeypatch, fake_principal):
    """AGENT_SEARCH_ENABLED=False → 404 before any DB hit."""
    app = _build_app(fake_principal=fake_principal, agent_search_enabled=False)
    monkeypatch.setattr(
        internal_retrieval_router,
        'Knowledges',
        SimpleNamespace(
            get_knowledge_by_id=AsyncMock(side_effect=AssertionError('should not run when disabled')),
        ),
    )
    client = TestClient(app)
    resp = client.get('/api/v1/internal/retrieval/knowledge/kb-1/files')
    assert resp.status_code == 404


def test_knowledge_files_404_when_kb_not_found(monkeypatch, fake_principal):
    """Unknown KB id → 404 (semantic match, not the 400 the user-facing route returns)."""
    app = _build_app(fake_principal=fake_principal)
    monkeypatch.setattr(
        internal_retrieval_router,
        'Knowledges',
        SimpleNamespace(get_knowledge_by_id=AsyncMock(return_value=None)),
    )
    client = TestClient(app)
    resp = client.get('/api/v1/internal/retrieval/knowledge/missing/files')
    assert resp.status_code == 404


def test_knowledge_files_401_when_bearer_missing(monkeypatch):
    """End-to-end auth: no/wrong bearer → 401 via the real ``get_agent_principal``.

    Wires the real dependency (not the test-only override) so the route's
    auth gate is verified for this surface specifically. The dependency's
    underlying behavior is exhaustively tested in ``test_service_auth.py``.
    """
    from open_webui.utils import service_auth

    monkeypatch.setenv('AGENT_API_KEY', 'correct-horse-' + 'b' * 24)

    app = FastAPI()
    app.include_router(internal_retrieval_router.router, prefix='/api/v1/internal/retrieval')
    app.state.config = SimpleNamespace(AGENT_SEARCH_ENABLED=True)

    # Stub Users.get_user_by_id so a valid bearer + acting user could resolve;
    # we still verify the 401 path here, but this keeps the fixture realistic.
    async def fake_get_user_by_id(user_id, db=None):
        return None

    monkeypatch.setattr(service_auth.Users, 'get_user_by_id', fake_get_user_by_id)

    client = TestClient(app, raise_server_exceptions=False)
    # No Authorization header at all.
    resp = client.get(
        '/api/v1/internal/retrieval/knowledge/kb-1/files',
        headers={'X-Acting-User-Id': 'user-uuid-1'},
    )
    assert resp.status_code == 401
    # Wrong bearer.
    resp = client.get(
        '/api/v1/internal/retrieval/knowledge/kb-1/files',
        headers={
            'Authorization': 'Bearer not-the-configured-key',
            'X-Acting-User-Id': 'user-uuid-1',
        },
    )
    assert resp.status_code == 401


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


# ---------- /files/{id}/raw (raw bytes for BIM agent) -------------------------


def _patch_files_and_storage(monkeypatch, *, file, storage_path):
    """Wire ``Files.get_file_by_id`` + ``Storage.get_file`` to a fake file."""

    class _FakeFiles:
        @staticmethod
        def get_file_by_id(file_id):
            return file if file_id == file.id else None

    monkeypatch.setattr(internal_retrieval_router, 'Files', _FakeFiles)
    monkeypatch.setattr(
        internal_retrieval_router.Storage,
        'get_file',
        staticmethod(lambda path: str(storage_path)),
    )


def test_files_id_raw_streams_bytes_for_owner(monkeypatch, fake_principal, tmp_path):
    app = _build_app(fake_principal=fake_principal)
    blob = tmp_path / 'model.ifc'
    blob.write_bytes(b'IFC raw bytes \x00\x01')

    fake_file = SimpleNamespace(
        id='file-1',
        user_id=fake_principal.user.id,
        filename='model.ifc',
        path='backend-side-key',
        meta={'content_type': 'application/octet-stream'},
        data={},
    )
    _patch_files_and_storage(monkeypatch, file=fake_file, storage_path=blob)

    client = TestClient(app)
    resp = client.get('/api/v1/internal/retrieval/files/file-1/raw')
    assert resp.status_code == 200
    assert resp.content == b'IFC raw bytes \x00\x01'


def test_files_id_raw_403_when_no_access(monkeypatch, fake_principal, tmp_path):
    app = _build_app(fake_principal=fake_principal)
    fake_file = SimpleNamespace(
        id='file-2',
        user_id='other-user',
        filename='other.ifc',
        path='backend-side-key',
        meta={},
        data={},
    )
    _patch_files_and_storage(monkeypatch, file=fake_file, storage_path=tmp_path / 'unused')
    monkeypatch.setattr(
        internal_retrieval_router,
        'has_access_to_file',
        lambda *, file_id, access_type, user: False,
    )

    client = TestClient(app)
    resp = client.get('/api/v1/internal/retrieval/files/file-2/raw')
    assert resp.status_code == 403


def test_files_id_raw_404_when_missing(monkeypatch, fake_principal):
    app = _build_app(fake_principal=fake_principal)

    class _Empty:
        @staticmethod
        def get_file_by_id(file_id):
            return None

    monkeypatch.setattr(internal_retrieval_router, 'Files', _Empty)
    client = TestClient(app)
    resp = client.get('/api/v1/internal/retrieval/files/nope/raw')
    assert resp.status_code == 404


def test_files_id_raw_404_when_storage_raises(monkeypatch, fake_principal, tmp_path):
    # Cloud-storage backends raise RuntimeError on credential/config errors;
    # surface as 404 (mirrors the /attachments handling) instead of leaking
    # the traceback.
    app = _build_app(fake_principal=fake_principal)
    fake_file = SimpleNamespace(
        id='file-3',
        user_id=fake_principal.user.id,
        filename='m.ifc',
        path='backend-side-key',
        meta={},
        data={},
    )

    class _FakeFiles:
        @staticmethod
        def get_file_by_id(file_id):
            return fake_file if file_id == fake_file.id else None

    def _raise(_path):
        raise RuntimeError('S3 credentials missing')

    monkeypatch.setattr(internal_retrieval_router, 'Files', _FakeFiles)
    monkeypatch.setattr(
        internal_retrieval_router.Storage,
        'get_file',
        staticmethod(_raise),
    )

    client = TestClient(app)
    resp = client.get('/api/v1/internal/retrieval/files/file-3/raw')
    assert resp.status_code == 404


def test_files_id_raw_requires_agent_search_enabled(monkeypatch, fake_principal):
    app = _build_app(fake_principal=fake_principal, agent_search_enabled=False)
    client = TestClient(app)
    resp = client.get('/api/v1/internal/retrieval/files/anything/raw')
    assert resp.status_code == 404


# ---------- /files/upload (agent-pushed message-attached file) ---------------------


def _patch_files_upload(monkeypatch, *, file_id='file-render-1'):
    """Stub the deps used by ``/files/upload`` and return a call recorder.

    Replaces ``upload_file_handler`` (filesystem + DB write),
    ``Chats.insert_chat_files`` (chat-file link row), and ``sio.emit``
    (socket fanout) with collaborator-style fakes. Returns the dict the
    test asserts against.
    """

    captured: dict = {}

    def fake_upload_file_handler(request, *, file, metadata, process, user, db):
        captured['filename'] = file.filename
        captured['content_type'] = file.content_type
        captured['process'] = process
        captured['metadata'] = metadata
        captured['user_id'] = user.id
        return SimpleNamespace(id=file_id)

    def fake_insert_chat_files(*, chat_id, message_id, file_ids, user_id, db=None):
        captured['insert'] = {
            'chat_id': chat_id,
            'message_id': message_id,
            'file_ids': list(file_ids),
            'user_id': user_id,
        }
        return None

    async def fake_emit(event, payload, room=None):
        captured['emit'] = {'event': event, 'payload': payload, 'room': room}

    monkeypatch.setattr(internal_retrieval_router, 'upload_file_handler', fake_upload_file_handler)
    monkeypatch.setattr(internal_retrieval_router.Chats, 'insert_chat_files', fake_insert_chat_files)
    monkeypatch.setattr(internal_retrieval_router.sio, 'emit', fake_emit)
    return captured


def test_post_files_upload_persists_and_attaches_to_message(monkeypatch, fake_principal):
    app = _build_app(fake_principal=fake_principal)
    # Ensure the route can resolve a URL for the file_id without booting all OWUI routers.
    app.add_api_route('/api/v1/files/{id}/content', lambda id: None, name='get_file_content_by_id')

    captured = _patch_files_upload(monkeypatch)

    client = TestClient(app)
    resp = client.post(
        '/api/v1/internal/retrieval/files/upload',
        data={'chat_id': 'chat-abc', 'message_id': 'msg-xyz'},
        files={'file': ('plan.png', b'\x89PNG\r\n\x1a\nfakepng', 'image/png')},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {
        'file_id': 'file-render-1',
        'url': '/api/v1/files/file-render-1/content',
    }
    assert captured['filename'] == 'plan.png'
    assert captured['content_type'] == 'image/png'
    assert captured['process'] is False
    assert captured['metadata'] == {'chat_id': 'chat-abc', 'message_id': 'msg-xyz'}
    assert captured['insert'] == {
        'chat_id': 'chat-abc',
        'message_id': 'msg-xyz',
        'file_ids': ['file-render-1'],
        'user_id': fake_principal.user.id,
    }
    assert captured['emit']['event'] == 'events'
    assert captured['emit']['room'] == f'user:{fake_principal.user.id}'
    emit_payload = captured['emit']['payload']
    assert emit_payload['chat_id'] == 'chat-abc'
    assert emit_payload['message_id'] == 'msg-xyz'
    inner = emit_payload['data']
    assert inner['type'] == 'chat:message:files'
    assert inner['data']['files'] == [
        {
            'type': 'image',
            'url': '/api/v1/files/file-render-1/content',
            'name': 'plan.png',
            'id': 'file-render-1',
        }
    ]


def test_post_files_upload_returns_404_when_feature_flag_disabled(monkeypatch, fake_principal):
    app = _build_app(fake_principal=fake_principal, agent_search_enabled=False)
    app.add_api_route('/api/v1/files/{id}/content', lambda id: None, name='get_file_content_by_id')

    def fake_upload_file_handler(*args, **kwargs):
        raise AssertionError('upload_file_handler should not run when disabled')

    monkeypatch.setattr(internal_retrieval_router, 'upload_file_handler', fake_upload_file_handler)

    client = TestClient(app)
    resp = client.post(
        '/api/v1/internal/retrieval/files/upload',
        data={'chat_id': 'chat-abc', 'message_id': 'msg-xyz'},
        files={'file': ('plan.png', b'fake', 'image/png')},
    )
    assert resp.status_code == 404
