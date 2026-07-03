"""Unit tests for the agent retrieval pipeline.

``run_agent_search`` is the single seam that the new
``/api/v1/internal/retrieval/query`` endpoint and any future ACL extension
share. We monkey-patch ``Knowledges.get_knowledge_bases_by_user_id`` and
``query_collection`` so the test exercises ACL→iteration→merge wiring
without spinning up Postgres or Weaviate.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from open_webui.services.retrieval import agent_search


@pytest.fixture
def fake_user():
    user = MagicMock()
    user.id = 'user-uuid-1'
    user.role = 'user'
    user.name = 'Lex'
    return user


def _kb(kb_id: str):
    kb = MagicMock()
    kb.id = kb_id
    return kb


@pytest.fixture
def fake_request():
    """Minimal request stub with EMBEDDING_FUNCTION on app.state."""
    request = MagicMock()
    request.app.state.EMBEDDING_FUNCTION = AsyncMock(return_value=[[0.1] * 8])
    return request


def _patch_knowledges(monkeypatch, *, kbs, suspended_ids=None, owned_kbs=None):
    suspended = set(suspended_ids or [])
    owned = list(owned_kbs) if owned_kbs is not None else []

    async def _get_kbs(user_id, permission='read', db=None):
        return kbs

    async def _get_owned(user_id, db=None):
        return owned

    async def _is_suspended(kb_id):
        return kb_id in suspended

    monkeypatch.setattr(agent_search.Knowledges, 'get_knowledge_bases_by_user_id', _get_kbs)
    monkeypatch.setattr(agent_search.Knowledges, 'get_knowledge_items_by_user_id', _get_owned)
    monkeypatch.setattr(agent_search.Knowledges, 'is_suspended', _is_suspended)


def _qr(distances, documents, metadatas):
    return {
        'distances': [distances],
        'documents': [documents],
        'metadatas': [metadatas],
    }


@pytest.mark.asyncio
async def test_run_agent_search_returns_chunks_from_accessible_kbs(monkeypatch, fake_user, fake_request):
    _patch_knowledges(monkeypatch, kbs=[_kb('kb-shared'), _kb('kb-alice')])

    per_kb = {
        'kb-shared': _qr(
            distances=[0.9, 0.5],
            documents=['shared-chunk-a', 'shared-chunk-b'],
            metadatas=[{'file_id': 'f1'}, {'file_id': 'f2'}],
        ),
        'kb-alice': _qr(
            distances=[0.7],
            documents=['alice-chunk'],
            metadatas=[{'file_id': 'f3'}],
        ),
    }

    async def fake_query_collection(request, *, collection_names, queries, embedding_function, k):
        assert len(collection_names) == 1
        return per_kb[collection_names[0]]

    monkeypatch.setattr(agent_search, 'query_collection', fake_query_collection)

    results = await agent_search.run_agent_search(
        request=fake_request,
        user=fake_user,
        query='hello',
        top_k=10,
    )

    # Sorted by score desc and tagged with kb_id provenance.
    assert [r['chunk'] for r in results] == ['shared-chunk-a', 'alice-chunk', 'shared-chunk-b']
    assert [r['kb_id'] for r in results] == ['kb-shared', 'kb-alice', 'kb-shared']
    assert [r['file_id'] for r in results] == ['f1', 'f3', 'f2']
    assert all(isinstance(r['score'], float) for r in results)


@pytest.mark.asyncio
async def test_run_agent_search_filters_by_kb_ids(monkeypatch, fake_user, fake_request):
    _patch_knowledges(monkeypatch, kbs=[_kb('kb-shared'), _kb('kb-alice')])

    queried = []

    async def fake_query_collection(request, *, collection_names, queries, embedding_function, k):
        queried.append(collection_names[0])
        return _qr([0.5], ['some-chunk'], [{'file_id': 'fX'}])

    monkeypatch.setattr(agent_search, 'query_collection', fake_query_collection)

    results = await agent_search.run_agent_search(
        request=fake_request,
        user=fake_user,
        query='hello',
        top_k=10,
        kb_ids=['kb-alice'],
    )

    assert queried == ['kb-alice']
    assert all(r['kb_id'] == 'kb-alice' for r in results)


@pytest.mark.asyncio
async def test_run_agent_search_skips_suspended_kbs(monkeypatch, fake_user, fake_request):
    _patch_knowledges(monkeypatch, kbs=[_kb('kb-active'), _kb('kb-frozen')], suspended_ids={'kb-frozen'})

    queried = []

    async def fake_query_collection(request, *, collection_names, queries, embedding_function, k):
        queried.append(collection_names[0])
        return _qr([0.3], ['c'], [{'file_id': 'fa'}])

    monkeypatch.setattr(agent_search, 'query_collection', fake_query_collection)

    await agent_search.run_agent_search(request=fake_request, user=fake_user, query='hi', top_k=5)

    assert queried == ['kb-active']


@pytest.mark.asyncio
async def test_run_agent_search_truncates_to_top_k(monkeypatch, fake_user, fake_request):
    _patch_knowledges(monkeypatch, kbs=[_kb('kb-1'), _kb('kb-2')])

    per_kb = {
        'kb-1': _qr([0.95, 0.7], ['c1', 'c2'], [{'file_id': 'a'}, {'file_id': 'b'}]),
        'kb-2': _qr([0.9, 0.6], ['c3', 'c4'], [{'file_id': 'c'}, {'file_id': 'd'}]),
    }

    async def fake_query_collection(request, *, collection_names, queries, embedding_function, k):
        return per_kb[collection_names[0]]

    monkeypatch.setattr(agent_search, 'query_collection', fake_query_collection)

    results = await agent_search.run_agent_search(request=fake_request, user=fake_user, query='hi', top_k=2)

    assert len(results) == 2
    assert [r['chunk'] for r in results] == ['c1', 'c3']


@pytest.mark.asyncio
async def test_run_agent_search_swallows_per_kb_failures(monkeypatch, fake_user, fake_request):
    _patch_knowledges(monkeypatch, kbs=[_kb('kb-good'), _kb('kb-broken')])

    async def fake_query_collection(request, *, collection_names, queries, embedding_function, k):
        if collection_names[0] == 'kb-broken':
            raise RuntimeError('weaviate down')
        return _qr([0.4], ['ok-chunk'], [{'file_id': 'fx'}])

    monkeypatch.setattr(agent_search, 'query_collection', fake_query_collection)

    results = await agent_search.run_agent_search(request=fake_request, user=fake_user, query='hi', top_k=10)

    # The healthy KB still returns its chunks; broken one is logged & skipped.
    assert len(results) == 1
    assert results[0]['kb_id'] == 'kb-good'


@pytest.mark.asyncio
async def test_run_agent_search_returns_empty_when_no_accessible_kbs(monkeypatch, fake_user, fake_request):
    _patch_knowledges(monkeypatch, kbs=[])
    # query_collection should never be called; provide a sentinel that would fail if it is.
    monkeypatch.setattr(
        agent_search,
        'query_collection',
        AsyncMock(side_effect=AssertionError('should not be called')),
    )
    results = await agent_search.run_agent_search(request=fake_request, user=fake_user, query='hi', top_k=10)
    assert results == []


# ---------- resolve_accessible_kbs ---------------------------------------------------


def _patch_sanitiser(monkeypatch, fn=None):
    """Force the sanitiser to mimic the Weaviate rule (capitalise + underscore)."""

    def _default(name: str) -> str:
        out = name.replace('-', '_')
        return out[:1].upper() + out[1:] if out else out

    sanitiser = fn or _default

    class _StubClient:
        def _sanitize_collection_name(self, name: str) -> str:
            return sanitiser(name)

    monkeypatch.setattr(agent_search, 'VECTOR_DB_CLIENT', _StubClient())


def _kb_with_meta(kb_id: str, *, name: str, description: str = '', owner_id: str = 'owner-1', type_: str = 'local'):
    kb = MagicMock()
    kb.id = kb_id
    kb.name = name
    kb.description = description
    kb.user_id = owner_id
    kb.type = type_
    return kb


@pytest.mark.asyncio
async def test_resolve_accessible_kbs_returns_sanitised_collection_names(monkeypatch, fake_user):
    _patch_knowledges(
        monkeypatch,
        kbs=[
            _kb_with_meta('abc-123', name='Wetten', description='Dutch laws'),
            _kb_with_meta('def-456', name='Beleid'),
        ],
    )
    _patch_sanitiser(monkeypatch)

    payload = await agent_search.resolve_accessible_kbs(fake_user)

    assert payload['user_id'] == fake_user.id
    assert payload['kb_index_collection_name'] == 'Knowledge_bases'
    assert [kb['id'] for kb in payload['kbs']] == ['abc-123', 'def-456']
    assert [kb['collection_name'] for kb in payload['kbs']] == ['Abc_123', 'Def_456']
    assert payload['kbs'][0]['name'] == 'Wetten'
    assert payload['kbs'][0]['description'] == 'Dutch laws'
    assert payload['kbs'][1]['description'] == ''


@pytest.mark.asyncio
async def test_resolve_accessible_kbs_drops_suspended(monkeypatch, fake_user):
    _patch_knowledges(
        monkeypatch,
        kbs=[
            _kb_with_meta('kb-active', name='A'),
            _kb_with_meta('kb-frozen', name='B'),
        ],
        suspended_ids={'kb-frozen'},
    )
    _patch_sanitiser(monkeypatch)

    payload = await agent_search.resolve_accessible_kbs(fake_user)
    assert [kb['id'] for kb in payload['kbs']] == ['kb-active']


@pytest.mark.asyncio
async def test_resolve_accessible_kbs_filters_by_kb_ids_subset(monkeypatch, fake_user):
    _patch_knowledges(
        monkeypatch,
        kbs=[
            _kb_with_meta('kb-1', name='One'),
            _kb_with_meta('kb-2', name='Two'),
            _kb_with_meta('kb-3', name='Three'),
        ],
    )
    _patch_sanitiser(monkeypatch)

    payload = await agent_search.resolve_accessible_kbs(fake_user, kb_ids=['kb-1', 'kb-3'])
    assert [kb['id'] for kb in payload['kbs']] == ['kb-1', 'kb-3']


@pytest.mark.asyncio
async def test_resolve_accessible_kbs_falls_back_when_sanitiser_missing(monkeypatch, fake_user):
    _patch_knowledges(monkeypatch, kbs=[_kb_with_meta('abc-123', name='X')])

    class _NoSanitise:
        pass

    monkeypatch.setattr(agent_search, 'VECTOR_DB_CLIENT', _NoSanitise())

    payload = await agent_search.resolve_accessible_kbs(fake_user)
    # No sanitiser → pass-through.
    assert payload['kbs'][0]['collection_name'] == 'abc-123'
    assert payload['kb_index_collection_name'] == 'knowledge-bases'


@pytest.mark.asyncio
async def test_owner_sees_owned_kb_even_when_grants_path_returns_empty(monkeypatch, fake_user):
    """Defensive ownership union: regression for the kbs:null observation
    where ``get_knowledge_bases_by_user_id`` returned empty for a KB owner.
    Ownership must always be visible regardless of grant-path state.
    """
    owned = _kb_with_meta('kb-owned', name='Owned', owner_id=fake_user.id)
    _patch_knowledges(monkeypatch, kbs=[], owned_kbs=[owned])
    _patch_sanitiser(monkeypatch)

    payload = await agent_search.resolve_accessible_kbs(fake_user)

    ids = [kb['id'] for kb in payload['kbs']]
    assert 'kb-owned' in ids


@pytest.mark.asyncio
async def test_owner_kb_present_via_both_paths_is_not_duplicated(monkeypatch, fake_user):
    """Dedup: a KB returned by both the grant pass and the ownership pass
    appears exactly once in the resolved set.
    """
    owned = _kb_with_meta('kb-owned', name='Owned', owner_id=fake_user.id)
    _patch_knowledges(monkeypatch, kbs=[owned], owned_kbs=[owned])
    _patch_sanitiser(monkeypatch)

    payload = await agent_search.resolve_accessible_kbs(fake_user)

    ids = [kb['id'] for kb in payload['kbs']]
    assert ids.count('kb-owned') == 1
