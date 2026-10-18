"""Schema drift guard for the MT connector's canonical property list.

Auto-schema is off on the MT collections, so any retrievable chunk-metadata key
must be declared in `_mt_properties()` — undeclared keys are silently dropped
at insert. The legacy connector's list gained `source_url` (citation links,
PR #195) while the MT copy drifted; this pins the property so it cannot drift
out again now that `_mt_properties()` is the only declaration.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import weaviate

from open_webui.retrieval.vector.dbs.weaviate_multitenancy import (
    WeaviateClient,
    _mt_properties,
)


def test_mt_properties_declare_source_url_as_text() -> None:
    # weaviate-client's Property model stores the `data_type` kwarg as `dataType`.
    props = {prop.name: prop.dataType for prop in _mt_properties()}
    assert props.get('source_url') == weaviate.classes.config.DataType.TEXT


def test_mt_properties_declare_bboxes_as_text() -> None:
    """Citation bbox geometry rides as a JSON string in a TEXT property."""
    props = {prop.name: prop.dataType for prop in _mt_properties()}
    assert props.get('bboxes') == weaviate.classes.config.DataType.TEXT


def test_mt_properties_keep_core_provenance_fields() -> None:
    """The fields the citation modal / soev-agents read must stay declared."""
    names = {prop.name for prop in _mt_properties()}
    assert {'text', 'file_id', 'name', 'source', 'source_url', 'created_by'} <= names


def test_mt_properties_declare_chunk_index_as_number() -> None:
    """Full-document reconstruction sorts chunks on `chunk_index`.

    Declared NUMBER (float64) to match Weaviate's inference for the ints OWUI
    writes, like the neighbouring splitter metadata.
    """
    props = {prop.name: prop.dataType for prop in _mt_properties()}
    assert props.get('chunk_index') == weaviate.classes.config.DataType.NUMBER


def test_ensure_declared_properties_adds_missing_property_to_existing_collection() -> None:
    """Collections created before a property was declared get it added in place.

    Auto-schema is off, so without this an existing collection silently drops
    `chunk_index` at insert.
    """
    collection = MagicMock()
    collection.config.get.return_value = SimpleNamespace(
        properties=[SimpleNamespace(name=prop.name) for prop in _mt_properties() if prop.name != 'chunk_index']
    )
    client = MagicMock()
    client.collections.get.return_value = collection
    fake_self = SimpleNamespace(client=client, _properties_ensured=set())

    WeaviateClient._ensure_declared_properties(fake_self, 'File')

    added = [call.args[0].name for call in collection.config.add_property.call_args_list]
    assert added == ['chunk_index']
    assert 'File' in fake_self._properties_ensured

    # Cached: a second call re-checks nothing.
    WeaviateClient._ensure_declared_properties(fake_self, 'File')
    assert collection.config.add_property.call_count == 1
