"""Schema drift guard for the MT connector's canonical property list.

Auto-schema is off on the MT collections, so any retrievable chunk-metadata key
must be declared in `_mt_properties()` — undeclared keys are silently dropped
at insert. The legacy connector's list gained `source_url` (citation links,
PR #195) while the MT copy drifted; this pins the property so it cannot drift
out again now that `_mt_properties()` is the only declaration.
"""

import weaviate

from open_webui.retrieval.vector.dbs.weaviate_multitenancy import _mt_properties


def test_mt_properties_declare_source_url_as_text() -> None:
    # weaviate-client's Property model stores the `data_type` kwarg as `dataType`.
    props = {prop.name: prop.dataType for prop in _mt_properties()}
    assert props.get('source_url') == weaviate.classes.config.DataType.TEXT


def test_mt_properties_keep_core_provenance_fields() -> None:
    """The fields the citation modal / soev-agents read must stay declared."""
    names = {prop.name for prop in _mt_properties()}
    assert {'text', 'file_id', 'name', 'source', 'source_url', 'created_by'} <= names
