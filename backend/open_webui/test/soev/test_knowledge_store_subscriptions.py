"""Subscribed corpus documents are projected as members of the requesting KB."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from open_webui.soev.client import SoevClient
from open_webui.soev.knowledge_store import SoevKnowledgeTable


@pytest.fixture
def subscribed_store(monkeypatch, identity_config, identity_http):
    """Serve an owned page and a reached corpus page through the real paging client."""
    from open_webui.models.files import Files

    identity, _ = identity_config
    monkeypatch.setattr(identity, 'ensure_link', AsyncMock())
    monkeypatch.setattr(Files, 'get_unlanded_files_for_collection', AsyncMock(return_value=[]))
    requests, responses = identity_http
    documents = [
        {'collection_key': 'kb', 'source_id': 'uploaded', 'path': 'notes.txt'},
        {'collection_key': 'kb', 'source_id': 'onedrive:item-1', 'path': 'shared/report.pdf'},
        {'collection_key': 'kb', 'source_id': 'google_drive:item-2', 'path': 'shared/budget.csv'},
    ]
    client = SoevClient('https://soev.invalid', 'test-key', subject_minter=lambda ref: 'test-assertion')
    return SimpleNamespace(
        store=SoevKnowledgeTable(client=client, service_principal='owui:service:webui'),
        requests=requests,
        responses=responses,
        documents=documents,
    )


@pytest.mark.asyncio
async def test_members_list_reached_documents_under_the_kb(subscribed_store):
    """Members retain reached documents and pass the composite cursor through opaquely."""
    env = subscribed_store
    cursor = 'corpus:drive|after/item+1='
    env.responses.extend(
        httpx.Response(200, json=body)
        for body in [
            {'data': env.documents[:1], 'next_cursor': cursor},
            {'data': env.documents[1:], 'next_cursor': None},
            {'data': [], 'next_cursor': None},
            {'data': [], 'next_cursor': None},
        ]
    )
    members = await env.store._members('kb', user_id='alice')
    assert members == {doc['source_id']: (doc, doc['path']) for doc in env.documents}
    assert [request.url.path for request in env.requests[:2]] == ['/v1/collections/kb/documents'] * 2
    assert env.requests[1].url.params['cursor'] == cursor
    assert all(request.headers['X-Soev-Subject'] == 'test-assertion' for request in env.requests)
    assert not env.responses


@pytest.mark.asyncio
async def test_counts_include_reached_documents(subscribed_store):
    """The visible collection count includes both owned and subscribed corpus documents."""
    env = subscribed_store
    env.responses.append(httpx.Response(200, json={'key': 'kb', 'document_count': len(env.documents)}))
    assert await env.store.get_file_counts_by_knowledge_ids(['kb'], user_id='alice') == {'kb': 3}
    assert len(env.requests) == 1
    assert env.requests[0].url.path == '/v1/collections/kb'
    assert env.requests[0].headers['X-Soev-Subject'] == 'test-assertion'
    assert not env.responses
