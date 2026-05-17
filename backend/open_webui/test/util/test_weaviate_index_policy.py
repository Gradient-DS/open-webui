"""Tests for the Weaviate per-collection vector-index policy.

Per-file (`File_*`), per-web-search (`Web_search_*`), and per-user-memory
(`User_memory_*`) classes hold at most a few thousand vectors each. We use
`flat` with binary quantization for them — knowledge-base classes (raw UUID
names) keep the HNSW default. See
thoughts/shared/plans/2026-05-17-weaviate-flat-index-migration.md.
"""

import pytest

from open_webui.retrieval.vector.dbs.weaviate import (
    _FLAT_INDEX_PREFIXES,
    _build_vector_config,
)


def _serialized(sane_name: str) -> dict:
    return _build_vector_config(sane_name)._to_dict()


class TestVectorIndexPolicy:
    @pytest.mark.parametrize(
        'sane_name',
        [
            'File_abc123',
            'File_550e8400_e29b_41d4_a716_446655440000',
            'Web_search_deadbeef',
            'User_memory_user_42',
        ],
    )
    def test_targeted_prefixes_get_flat_with_bq(self, sane_name: str) -> None:
        payload = _serialized(sane_name)
        assert payload['vectorIndexType'] == 'flat'
        assert payload['vectorIndexConfig'] == {'bq': {'enabled': True}}

    @pytest.mark.parametrize(
        'sane_name',
        [
            'Abc123def456',
            'KnowledgeBaseClass',
            'Filer_lookalike',
            'Web_lookalike',
            'User_other',
        ],
    )
    def test_other_classes_keep_default_hnsw(self, sane_name: str) -> None:
        payload = _serialized(sane_name)
        # Default = HNSW with no per-class override (server-side defaults apply).
        assert payload.get('vectorIndexType', 'hnsw') == 'hnsw'
        assert 'bq' not in (payload.get('vectorIndexConfig') or {})

    def test_prefix_list_matches_plan(self) -> None:
        assert _FLAT_INDEX_PREFIXES == ('File_', 'Web_search_', 'User_memory_')
