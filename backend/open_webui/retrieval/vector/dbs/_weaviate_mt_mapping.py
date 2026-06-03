"""Weaviate multi-tenancy collection-name mapping.

Maps an OWUI logical collection_name to (mt_collection, tenant).

WARNING: coupled to OWUI naming conventions (user-memory-/file-/web-search-/
KB-UUID/hash). Changing OWUI naming without updating this AND the genai-utils
copy risks routing data to the wrong tenant — data corruption.
"""

KNOWLEDGE, FILE, WEB_SEARCH, USER_MEMORY, HASH_BASED = (
    'Knowledge',
    'File',
    'WebSearch',
    'UserMemory',
    'HashBased',
)
KNOWLEDGE_BASES_META = 'Knowledge_bases'  # standalone, NOT multi-tenant


def map_collection(collection_name: str) -> tuple[str, str | None]:
    """Map an OWUI logical collection name to a (mt_collection, tenant) pair.

    The tenant key is the raw ``collection_name`` value — no transformation is
    applied, mirroring the qdrant/milvus approach where ``tenant_id =
    collection_name``.

    ``knowledge-bases`` is the only case that returns ``tenant=None``; it
    remains a standalone non-multi-tenant collection (the legacy code sanitises
    it to the class ``Knowledge_bases``).

    Args:
        collection_name: The logical collection name used throughout OWUI.

    Returns:
        A ``(mt_collection, tenant)`` tuple where ``tenant`` is ``None`` only
        for the ``knowledge-bases`` meta-index.
    """
    name = collection_name
    if name == 'knowledge-bases':
        return KNOWLEDGE_BASES_META, None  # meta-index: no tenant
    if name.startswith('user-memory-'):
        return USER_MEMORY, name
    if name.startswith('file-'):
        return FILE, name
    if name.startswith('web-search-'):
        return WEB_SEARCH, name
    if len(name) in (63, 64) and all(c in '0123456789abcdef' for c in name):
        return HASH_BASED, name  # URL/YouTube/text hash
    return KNOWLEDGE, name  # KB UUID
