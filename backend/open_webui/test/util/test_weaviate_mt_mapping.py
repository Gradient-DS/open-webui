"""Tests for the Weaviate multi-tenancy collection-name mapping function.

Covers every branch of ``map_collection``, including edge cases that must NOT
match the hash-based path (non-hex chars, wrong lengths).
See thoughts/shared/plans/ for the broader multi-tenancy refactor context.
"""

import pytest

from open_webui.retrieval.vector.dbs._weaviate_mt_mapping import (
    FILE,
    HASH_BASED,
    KNOWLEDGE,
    KNOWLEDGE_BASES_META,
    USER_MEMORY,
    WEB_SEARCH,
    map_collection,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hex(length: int) -> str:
    """Return a lowercase hex string of exactly *length* chars."""
    return ('deadbeef' * 10)[:length]


# ---------------------------------------------------------------------------
# knowledge-bases meta-index
# ---------------------------------------------------------------------------


def test_knowledge_bases_meta_index() -> None:
    mt_col, tenant = map_collection('knowledge-bases')
    assert mt_col == KNOWLEDGE_BASES_META
    assert tenant is None


# ---------------------------------------------------------------------------
# user-memory-* prefix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    'name',
    [
        'user-memory-42',
        'user-memory-abc-def',
        'user-memory-550e8400-e29b-41d4-a716-446655440000',
    ],
)
def test_user_memory_prefix(name: str) -> None:
    mt_col, tenant = map_collection(name)
    assert mt_col == USER_MEMORY
    assert tenant == name  # raw, unmodified


# ---------------------------------------------------------------------------
# file-* prefix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    'name',
    [
        'file-123e4567-e89b-12d3-a456-426614174000',
        'file-abc',
        'file-550e8400_e29b_41d4_a716_446655440000',
    ],
)
def test_file_prefix(name: str) -> None:
    mt_col, tenant = map_collection(name)
    assert mt_col == FILE
    assert tenant == name


# ---------------------------------------------------------------------------
# web-search-* prefix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    'name',
    [
        'web-search-deadbeef',
        'web-search-abc123',
        'web-search-' + _hex(63),
    ],
)
def test_web_search_prefix(name: str) -> None:
    mt_col, tenant = map_collection(name)
    assert mt_col == WEB_SEARCH
    assert tenant == name


# ---------------------------------------------------------------------------
# hash-based (URL / YouTube / text-content hashes)
# ---------------------------------------------------------------------------


def test_hash_based_63_chars() -> None:
    name = _hex(63)
    mt_col, tenant = map_collection(name)
    assert mt_col == HASH_BASED
    assert tenant == name


def test_hash_based_64_chars() -> None:
    """64-char hex is valid; distinguishes us from qdrant's 63-only check."""
    name = _hex(64)
    mt_col, tenant = map_collection(name)
    assert mt_col == HASH_BASED
    assert tenant == name


# ---------------------------------------------------------------------------
# KB UUID fallthrough → Knowledge
# ---------------------------------------------------------------------------


def test_kb_uuid_falls_through_to_knowledge() -> None:
    """A standard UUID (36 chars, contains hyphens) must NOT match hash."""
    name = '123e4567-e89b-12d3-a456-426614174000'
    assert len(name) == 36  # sanity: too short for hash, has hyphens
    mt_col, tenant = map_collection(name)
    assert mt_col == KNOWLEDGE
    assert tenant == name


@pytest.mark.parametrize(
    'name',
    [
        'some-random-knowledge-base-name',
        'kb-abcdef',
        'MyKnowledgeBase',
    ],
)
def test_arbitrary_names_fall_through_to_knowledge(name: str) -> None:
    mt_col, tenant = map_collection(name)
    assert mt_col == KNOWLEDGE
    assert tenant == name


# ---------------------------------------------------------------------------
# Edge cases: strings that must NOT match hash
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    'name,reason',
    [
        (_hex(62), '62 chars — too short'),
        (_hex(65), '65 chars — too long'),
        (_hex(62) + 'G', '63 chars but contains uppercase G — non-hex'),
        (_hex(63) + 'g', '64 chars but contains lowercase g — non-hex'),
        (_hex(62) + 'Z', '63 chars with non-hex uppercase Z'),
    ],
)
def test_non_matching_hex_like_strings_fall_through_to_knowledge(name: str, reason: str) -> None:
    mt_col, tenant = map_collection(name)
    assert mt_col == KNOWLEDGE, f'Expected Knowledge fallthrough ({reason}), got {mt_col!r}'
    assert tenant == name


# ---------------------------------------------------------------------------
# Tenant is always the raw unmodified input for non-meta branches
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    'name',
    [
        'user-memory-42',
        'file-abc',
        'web-search-xyz',
        _hex(63),
        _hex(64),
        'some-kb-name',
    ],
)
def test_tenant_is_always_raw_input(name: str) -> None:
    _, tenant = map_collection(name)
    assert tenant == name


# ---------------------------------------------------------------------------
# Constants sanity-check
# ---------------------------------------------------------------------------


def test_constant_values() -> None:
    assert KNOWLEDGE == 'Knowledge'
    assert FILE == 'File'
    assert WEB_SEARCH == 'WebSearch'
    assert USER_MEMORY == 'UserMemory'
    assert HASH_BASED == 'HashBased'
    assert KNOWLEDGE_BASES_META == 'Knowledge_bases'
