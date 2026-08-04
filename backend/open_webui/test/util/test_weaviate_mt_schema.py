"""Schema drift guard for the MT connector's canonical property list.

Auto-schema is off on the MT collections, so any retrievable chunk-metadata key
must be declared in `_mt_properties()` — undeclared keys are silently dropped
at insert. The legacy connector's list gained `source_url` (citation links,
PR #195) while the MT copy drifted; this pins the property so it cannot drift
out again now that `_mt_properties()` is the only declaration.
"""

import weaviate

from open_webui.retrieval.vector.dbs.weaviate_multitenancy import (
    _BACKFILL_PROPERTY_NAMES,
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


def test_backfill_property_names_are_declared_in_mt_properties() -> None:
    """Every back-filled property must also be in the canonical list, or fresh
    collections would miss what existing ones get patched with."""
    names = {prop.name for prop in _mt_properties()}
    assert set(_BACKFILL_PROPERTY_NAMES) <= names


def test_mt_properties_keep_core_provenance_fields() -> None:
    """The fields the citation modal / soev-agents read must stay declared."""
    names = {prop.name for prop in _mt_properties()}
    assert {'text', 'file_id', 'name', 'source', 'source_url', 'created_by'} <= names
