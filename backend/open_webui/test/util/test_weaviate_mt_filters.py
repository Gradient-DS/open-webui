# [Gradient] ACL filters must reach every MT read/delete path and fail closed.
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from weaviate.classes.query import Filter

from open_webui.retrieval.vector.dbs.weaviate_multitenancy import (
    WeaviateClient,
    _metadata_filter,
    _mt_properties,
)


def assert_equal(clause, key, value):
    assert clause.target == key
    assert clause.operator.value == 'Equal'
    assert clause.value == value


def test_in_combines_equal_clauses_with_any_of():
    clause = _metadata_filter({'knowledge_base_id': {'$in': ['a', 'b']}})
    expected = Filter.any_of([Filter.by_property('knowledge_base_id').equal(value) for value in ['a', 'b']])
    assert isinstance(clause, type(expected))
    assert len(clause.filters) == 2
    for child, value in zip(clause.filters, ['a', 'b']):
        assert_equal(child, 'knowledge_base_id', value)


@pytest.mark.parametrize('value', ['a', {'$eq': 'a'}])
def test_scalar_and_explicit_equality(value):
    assert_equal(_metadata_filter({'knowledge_base_id': value}), 'knowledge_base_id', 'a')


def test_multiple_properties_are_anded():
    clause = _metadata_filter({'file_id': 'f', 'hash': {'$in': ['h1', 'h2']}})
    expected = Filter.all_of([Filter.by_property('file_id').equal(value) for value in ['f', 'g']])
    assert isinstance(clause, type(expected))
    assert_equal(clause.filters[0], 'file_id', 'f')
    assert [child.value for child in clause.filters[1].filters] == ['h1', 'h2']


@pytest.fixture
def client():
    instance = WeaviateClient.__new__(WeaviateClient)
    instance._tenant_exists = MagicMock(return_value=True)
    queryable = MagicMock()
    queryable.query.near_vector.return_value = SimpleNamespace(objects=[])
    queryable.query.fetch_objects.return_value = SimpleNamespace(objects=[])
    instance._queryable = MagicMock(return_value=queryable)
    return instance


def test_search_forwards_acl_filter_for_every_vector(client):
    result = client.search('knowledge-bases', [[1, 0], [0, 1]], {'knowledge_base_id': {'$in': ['a', 'b']}}, limit=3)
    assert result.ids == [[], []]
    calls = client._queryable.return_value.query.near_vector.call_args_list
    assert len(calls) == 2
    for call, vector in zip(calls, [[1, 0], [0, 1]]):
        assert call.kwargs['near_vector'] == vector
        assert call.kwargs['limit'] == 3
        assert [child.value for child in call.kwargs['filters'].filters] == ['a', 'b']


def test_query_forwards_filter(client):
    client.query('knowledge-bases', {'knowledge_base_id': {'$in': ['a', 'b']}}, limit=7)
    call = client._queryable.return_value.query.fetch_objects.call_args
    assert call.kwargs['limit'] == 7
    assert [child.value for child in call.kwargs['filters'].filters] == ['a', 'b']


def test_delete_forwards_filter(client):
    client.delete('knowledge-bases', filter={'knowledge_base_id': {'$in': ['a', 'b']}})
    clause = client._queryable.return_value.data.delete_many.call_args.kwargs['where']
    assert [child.value for child in clause.filters] == ['a', 'b']


@pytest.mark.parametrize('operation', ['search', 'query', 'delete'])
@pytest.mark.parametrize('exists', [True, False])
def test_undeclared_properties_raise_before_querying(client, operation, exists):
    client._tenant_exists.return_value = exists
    kwargs = {'vectors': [[1, 0]]} if operation == 'search' else {}
    with pytest.raises(ValueError, match='Undeclared'):
        getattr(client, operation)('knowledge-bases', filter={'undeclared_property': 'x'}, **kwargs)
    client._queryable.assert_not_called()


@pytest.mark.parametrize(
    'filter',
    [
        {'file_id': {'$ne': 'a'}},
        {'file_id': {'$eq': 'a', '$in': ['b']}},
        {'file_id': {'$in': []}},
        {'file_id': {'$in': 'a'}},
    ],
)
@pytest.mark.parametrize('operation', ['search', 'query', 'delete'])
def test_invalid_filters_never_become_unfiltered_operations(client, filter, operation):
    kwargs = {'vectors': [[1, 0]]} if operation == 'search' else {}
    with pytest.raises(ValueError):
        getattr(client, operation)('knowledge-bases', filter=filter, **kwargs)
    client._queryable.assert_not_called()


def test_no_filter_preserves_unfiltered_search(client):
    client.search('knowledge-bases', [[1, 0]])
    assert client._queryable.return_value.query.near_vector.call_args.kwargs['filters'] is None


def test_filter_properties_are_declared():
    assert {'knowledge_base_id', 'hash'} <= {prop.name for prop in _mt_properties()}
