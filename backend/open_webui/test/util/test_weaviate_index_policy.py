"""Tests for the Weaviate per-collection vector-index policy.

Per-file (`File_*`), per-web-search (`Web_search_*`), and per-user-memory
(`User_memory_*`) classes can opt into the `flat` index with binary
quantization via ENABLE_WEAVIATE_BQ_QUANTIZATION (default off — HNSW for
every collection). See thoughts/shared/plans/2026-05-25-bq-disable-and-no-per-file-collections-for-kb-uploads.md.
"""

import pytest

from open_webui.retrieval.vector.dbs import weaviate as weaviate_module
from open_webui.retrieval.vector.dbs.weaviate import (
    _FLAT_INDEX_PREFIXES,
    _build_vector_config,
)


def _serialized(sane_name: str) -> dict:
    return _build_vector_config(sane_name)._to_dict()


class TestVectorIndexPolicyBqOff:
    """Default behavior: ENABLE_WEAVIATE_BQ_QUANTIZATION is false."""

    @pytest.fixture(autouse=True)
    def _bq_off(self, monkeypatch):
        monkeypatch.setattr(weaviate_module, 'ENABLE_WEAVIATE_BQ_QUANTIZATION', False)

    @pytest.mark.parametrize(
        'sane_name',
        [
            'File_abc123',
            'File_550e8400_e29b_41d4_a716_446655440000',
            'Web_search_deadbeef',
            'User_memory_user_42',
            'Abc123def456',
            'KnowledgeBaseClass',
        ],
    )
    def test_all_classes_default_hnsw_when_disabled(self, sane_name: str) -> None:
        payload = _serialized(sane_name)
        assert payload.get('vectorIndexType', 'hnsw') == 'hnsw'
        assert 'bq' not in (payload.get('vectorIndexConfig') or {})


class TestVectorIndexPolicyBqOn:
    """Opt-in behavior: ENABLE_WEAVIATE_BQ_QUANTIZATION is true."""

    @pytest.fixture(autouse=True)
    def _bq_on(self, monkeypatch):
        monkeypatch.setattr(weaviate_module, 'ENABLE_WEAVIATE_BQ_QUANTIZATION', True)

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
        assert payload.get('vectorIndexType', 'hnsw') == 'hnsw'
        assert 'bq' not in (payload.get('vectorIndexConfig') or {})


def test_prefix_list_matches_plan() -> None:
    assert _FLAT_INDEX_PREFIXES == ('File_', 'Web_search_', 'User_memory_')


# ---------------------------------------------------------------------------
# MT connector: _build_vector_config (keyed on exact MT collection names)
# ---------------------------------------------------------------------------

from open_webui.retrieval.vector.dbs.weaviate_multitenancy import (  # noqa: E402
    _MT_FLAT_COLLECTIONS,
    _build_vector_config as _mt_build_vector_config,
)
import open_webui.retrieval.vector.dbs.weaviate_multitenancy as weaviate_mt_module  # noqa: E402


def _mt_serialized(mt_collection_name: str) -> dict:
    return _mt_build_vector_config(mt_collection_name)._to_dict()


class TestMTVectorIndexPolicyBqOff:
    """Default behavior: ENABLE_WEAVIATE_BQ_QUANTIZATION is false.

    Every MT collection must fall back to the HNSW default regardless of name.
    """

    @pytest.fixture(autouse=True)
    def _bq_off(self, monkeypatch):
        monkeypatch.setattr(weaviate_mt_module, 'ENABLE_WEAVIATE_BQ_QUANTIZATION', False)

    @pytest.mark.parametrize(
        'mt_name',
        [
            'File',
            'WebSearch',
            'UserMemory',
            'Knowledge',
            'HashBased',
        ],
    )
    def test_all_mt_collections_default_hnsw_when_bq_disabled(self, mt_name: str) -> None:
        payload = _mt_serialized(mt_name)
        assert payload.get('vectorIndexType', 'hnsw') == 'hnsw'
        assert 'bq' not in (payload.get('vectorIndexConfig') or {})


class TestMTVectorIndexPolicyBqOn:
    """Opt-in behavior: ENABLE_WEAVIATE_BQ_QUANTIZATION is true.

    File / WebSearch / UserMemory → flat + BQ.
    Knowledge / HashBased → always HNSW.
    """

    @pytest.fixture(autouse=True)
    def _bq_on(self, monkeypatch):
        monkeypatch.setattr(weaviate_mt_module, 'ENABLE_WEAVIATE_BQ_QUANTIZATION', True)

    @pytest.mark.parametrize(
        'mt_name',
        [
            'File',
            'WebSearch',
            'UserMemory',
        ],
    )
    def test_flat_collections_get_flat_with_bq(self, mt_name: str) -> None:
        payload = _mt_serialized(mt_name)
        assert payload['vectorIndexType'] == 'flat'
        assert payload['vectorIndexConfig'] == {'bq': {'enabled': True}}

    @pytest.mark.parametrize(
        'mt_name',
        [
            'Knowledge',
            'HashBased',
        ],
    )
    def test_hnsw_collections_always_hnsw(self, mt_name: str) -> None:
        payload = _mt_serialized(mt_name)
        assert payload.get('vectorIndexType', 'hnsw') == 'hnsw'
        assert 'bq' not in (payload.get('vectorIndexConfig') or {})


def test_mt_flat_collections_constant() -> None:
    assert _MT_FLAT_COLLECTIONS == ('File', 'WebSearch', 'UserMemory')
