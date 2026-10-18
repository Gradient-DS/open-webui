"""Tests for the Weaviate MT per-collection vector-index policy.

The File / WebSearch / UserMemory schema collections opt into the `flat` index
with binary quantization via ENABLE_WEAVIATE_BQ_QUANTIZATION (default off —
HNSW for every collection). Knowledge / HashBased always get HNSW.
See thoughts/shared/plans/2026-05-25-bq-disable-and-no-per-file-collections-for-kb-uploads.md.
"""

import pytest

import open_webui.retrieval.vector.dbs.weaviate_multitenancy as weaviate_mt_module
from open_webui.retrieval.vector.dbs.weaviate_multitenancy import (
    _MT_FLAT_COLLECTIONS,
    _build_vector_config,
)


def _serialized(mt_collection_name: str) -> dict:
    return _build_vector_config(mt_collection_name)._to_dict()


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
        payload = _serialized(mt_name)
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
        payload = _serialized(mt_name)
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
        payload = _serialized(mt_name)
        assert payload.get('vectorIndexType', 'hnsw') == 'hnsw'
        assert 'bq' not in (payload.get('vectorIndexConfig') or {})


def test_mt_flat_collections_constant() -> None:
    assert _MT_FLAT_COLLECTIONS == ('File', 'WebSearch', 'UserMemory')
