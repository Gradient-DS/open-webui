"""Knowledge meta lives under OWUI's namespace in the collection's client-owned meta."""

import hashlib
import json
from unittest.mock import AsyncMock

import pytest
from open_webui.soev import projection
from open_webui.soev.client import SoevApiError
from open_webui.soev.knowledge_store import SoevKnowledgeTable

SERVICE = 'owui:service:webui'
EXTERNAL = {
    'source': 'external',
    'read_only': True,
    'external': {'connection_id': 'c1', 'source': {'path': '/a', 'depth': 2}, 'provider': 'p'},
}


def _collection(meta):
    return {
        'key': 'kb',
        'name': 'Research',
        'description': None,
        'created_by': 'owui:user:alice',
        'subscriptions': ['onedrive'],
        'visibility': 'restricted',
        'principals': [SERVICE, 'owui:user:alice'],
        'writers': ['owui:user:alice'],
        'meta': meta,
        'created_at': '2026-10-10T00:00:00+00:00',
        'updated_at': '2026-10-10T00:00:00+00:00',
    }


class _Client:
    """The soev client boundary: the stored collection it reads and the collection a write returns."""

    def __init__(self, stored):
        self.get = AsyncMock(return_value=stored)
        self.send = AsyncMock(return_value=_collection({'console': {'pinned': True}, 'owui': EXTERNAL}))


@pytest.mark.asyncio
async def test_meta_is_written_under_the_owui_namespace():
    """A first write patches only OWUI's key and returns the knowledge with that meta."""
    client = _Client(_collection({'console': {'pinned': True}}))
    store = SoevKnowledgeTable(client=client, service_principal=SERVICE)
    knowledge = await store.update_knowledge_meta_by_id('kb', EXTERNAL)
    body = {'meta': {'owui': EXTERNAL}}
    key = hashlib.sha256(
        json.dumps(['PATCH', '/v1/collections/kb', body], sort_keys=True, separators=(',', ':')).encode()
    ).hexdigest()
    client.send.assert_awaited_once_with('PATCH', '/v1/collections/kb', body, as_user=None, idempotency_key=key)
    assert knowledge.meta == EXTERNAL


@pytest.mark.asyncio
async def test_meta_replacement_deletes_what_the_new_meta_omits():
    """Replacing nulls removed keys at every depth and leaves other clients' keys out of the patch."""
    client = _Client(_collection({'console': {'pinned': True}, 'owui': {**EXTERNAL, 'onedrive_sync': {'sources': []}}}))
    store = SoevKnowledgeTable(client=client, service_principal=SERVICE)
    desired = {**EXTERNAL, 'external': {**EXTERNAL['external'], 'source': {'path': '/b'}}}
    await store.update_knowledge_meta_by_id('kb', desired)
    sent = client.send.await_args.args[2]
    assert sent == {'meta': {'owui': {'onedrive_sync': None, 'external': {'source': {'path': '/b', 'depth': None}}}}}


@pytest.mark.asyncio
async def test_meta_of_a_missing_collection_is_none():
    """A collection the caller cannot read is reported as absent and nothing is written."""
    client = _Client(None)
    client.get.side_effect = SoevApiError(404, 'collection_not_found', 'Not found')
    store = SoevKnowledgeTable(client=client, service_principal=SERVICE)
    assert await store.update_knowledge_meta_by_id('kb', EXTERNAL) is None
    client.send.assert_not_awaited()


def test_knowledge_reads_only_its_own_namespace():
    """The knowledge model shows OWUI's meta and never another client's."""
    knowledge = projection.knowledge_of(
        _collection({'console': {'pinned': True}, 'owui': EXTERNAL}), service_principal=SERVICE
    )
    assert knowledge.meta == EXTERNAL
    assert projection.knowledge_of(_collection({'console': {}}), service_principal=SERVICE).meta == {}


@pytest.mark.parametrize(
    ('current', 'desired', 'patch'),
    [
        ({}, {'a': 1}, {'a': 1}),
        ({'a': 1}, {'a': 1}, {}),
        ({'a': 1, 'b': 2}, {'a': 1}, {'b': None}),
        ({'a': {'x': 1, 'y': 2}}, {'a': {'x': 1}}, {'a': {'y': None}}),
        ({'a': {'x': 1}}, {'a': [1]}, {'a': [1]}),
        ({'a': 1}, {'a': None}, {'a': None}),
        ({}, {'a': None}, {}),
    ],
)
def test_meta_patch_turns_current_into_desired(current, desired, patch):
    """The patch is minimal and reads a desired None as an absent key."""
    assert projection.meta_patch(current, desired) == patch
