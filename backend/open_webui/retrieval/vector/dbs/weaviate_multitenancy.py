"""
NOTE: This vector database integration is community-supported and maintained on a best-effort basis.

Native Weaviate multi-tenancy connector. Routes every OWUI logical collection into
one of five fixed schema collections (Knowledge, File, WebSearch, UserMemory,
HashBased) as a native Weaviate TENANT (one shard + dedicated vector index per
tenant), plus a standalone non-multi-tenant meta collection (Knowledge_bases).

This mirrors the legacy ``weaviate.py`` connector for connection setup, property
schema, batching and read/serialize/normalize logic, but uses native tenants
(``.with_tenant(...)``) instead of one class per logical collection.

A dual-read shim lets reads/has_collection fall back to the legacy per-class data
during the migration window (controlled by WEAVIATE_MT_LEGACY_FALLBACK).
"""

import logging
import re
import uuid
from typing import Any, Dict, List, Optional, Union

import weaviate
from weaviate.exceptions import UnexpectedStatusCodeError

from open_webui.retrieval.vector.main import (
    VectorDBBase,
    VectorItem,
    SearchResult,
    GetResult,
)
from open_webui.retrieval.vector.utils import process_metadata

# Reuse the legacy helpers verbatim — do not duplicate.
from open_webui.retrieval.vector.dbs.weaviate import (
    _make_json_serializable,
    _sanitize_property_name,  # noqa: F401 (re-exported for parity/tests)
    _sanitize_metadata_keys,
)
from open_webui.retrieval.vector.dbs._weaviate_mt_mapping import (
    map_collection,
    KNOWLEDGE,
    FILE,
    WEB_SEARCH,
    USER_MEMORY,
    HASH_BASED,
    KNOWLEDGE_BASES_META,
)
from open_webui.config import (
    WEAVIATE_HTTP_HOST,
    WEAVIATE_GRPC_HOST,
    WEAVIATE_HTTP_PORT,
    WEAVIATE_GRPC_PORT,
    WEAVIATE_API_KEY,
    WEAVIATE_HTTP_SECURE,
    WEAVIATE_GRPC_SECURE,
    WEAVIATE_SKIP_INIT_CHECKS,
    ENABLE_WEAVIATE_BQ_QUANTIZATION,
    ENABLE_WEAVIATE_MULTITENANCY_MODE,  # noqa: F401 (read at module level for monkeypatch)
    WEAVIATE_MT_LEGACY_FALLBACK,
)

log = logging.getLogger(__name__)


# The MT schema collections that opt into the `flat` index with binary
# quantization when ENABLE_WEAVIATE_BQ_QUANTIZATION is true. When the flag is
# false (default) every collection falls through to Weaviate's HNSW default.
# Knowledge and HashBased always get HNSW (default).
_MT_FLAT_COLLECTIONS = (FILE, WEB_SEARCH, USER_MEMORY)


def _build_vector_config(mt_collection_name: str):
    """Pick the vector-index config for an MT schema collection by exact name.

    When ENABLE_WEAVIATE_BQ_QUANTIZATION is false (default), every collection
    falls through to the Weaviate HNSW default (self_provided). When true,
    File / WebSearch / UserMemory get the flat index with binary quantization.
    Mirrors legacy ``_build_vector_config`` but keyed on the exact MT collection
    names instead of class-name prefixes.
    """
    if ENABLE_WEAVIATE_BQ_QUANTIZATION and mt_collection_name in _MT_FLAT_COLLECTIONS:
        return weaviate.classes.config.Configure.Vectors.self_provided(
            vector_index_config=weaviate.classes.config.Configure.VectorIndex.flat(
                quantizer=weaviate.classes.config.Configure.VectorIndex.Quantizer.bq()
            )
        )
    return weaviate.classes.config.Configure.Vectors.self_provided()


def _legacy_sanitize_collection_name(collection_name: str) -> str:
    """Replicate the legacy ``_sanitize_collection_name`` regex EXACTLY.

    Gives the legacy Weaviate class name for a given logical collection_name, so
    the dual-read shim can locate (and, on delete, purge) the legacy class.
    """
    if not isinstance(collection_name, str) or not collection_name.strip():
        raise ValueError('Collection name must be a non-empty string')

    # Replace hyphens with underscores and keep only alphanumeric + underscore.
    name = re.sub(r'[^a-zA-Z0-9_]', '', collection_name.replace('-', '_'))
    name = name.strip('_')

    if not name:
        raise ValueError('Could not sanitize collection name to be a valid Weaviate class name')

    # Ensure it starts with a letter and is capitalized.
    if not name[0].isalpha():
        name = 'C' + name

    return name[0].upper() + name[1:]


# Full property list copied from legacy `_create_collection` (weaviate.py). Applied
# uniformly to all five MT collections and the meta collection: cloud-sync metadata
# on Knowledge/File requires it and it is harmless on the others. Explicitly typed
# as TEXT to prevent Weaviate auto-schema from inferring wrong types.
def _mt_properties() -> list:
    return [
        weaviate.classes.config.Property(name='text', data_type=weaviate.classes.config.DataType.TEXT),
        # Core file metadata - always present
        weaviate.classes.config.Property(name='file_id', data_type=weaviate.classes.config.DataType.TEXT),
        weaviate.classes.config.Property(name='name', data_type=weaviate.classes.config.DataType.TEXT),
        weaviate.classes.config.Property(name='source', data_type=weaviate.classes.config.DataType.TEXT),
        weaviate.classes.config.Property(name='created_by', data_type=weaviate.classes.config.DataType.TEXT),
        # PDF metadata - dates come in non-RFC3339 format
        weaviate.classes.config.Property(name='moddate', data_type=weaviate.classes.config.DataType.TEXT),
        weaviate.classes.config.Property(name='creationdate', data_type=weaviate.classes.config.DataType.TEXT),
        # OneDrive metadata
        weaviate.classes.config.Property(
            name='onedrive_item_id',
            data_type=weaviate.classes.config.DataType.TEXT,
        ),
        weaviate.classes.config.Property(
            name='onedrive_drive_id',
            data_type=weaviate.classes.config.DataType.TEXT,
        ),
    ]


class WeaviateClient(VectorDBBase):
    def __init__(self):
        self.url = WEAVIATE_HTTP_HOST
        # Read flags as instance attributes so tests can also monkeypatch the
        # module attribute (imported above) and so behaviour is explicit.
        self.legacy_fallback = WEAVIATE_MT_LEGACY_FALLBACK
        try:
            # Build connection parameters
            connection_params = {
                'http_host': WEAVIATE_HTTP_HOST,
                'http_port': WEAVIATE_HTTP_PORT,
                'http_secure': WEAVIATE_HTTP_SECURE,
                'grpc_host': WEAVIATE_GRPC_HOST,
                'grpc_port': WEAVIATE_GRPC_PORT,
                'grpc_secure': WEAVIATE_GRPC_SECURE,
                'skip_init_checks': WEAVIATE_SKIP_INIT_CHECKS,
            }

            # Only add auth_credentials if WEAVIATE_API_KEY exists and is not empty
            if WEAVIATE_API_KEY:
                connection_params['auth_credentials'] = weaviate.classes.init.Auth.api_key(WEAVIATE_API_KEY)

            self.client = weaviate.connect_to_custom(**connection_params)
            self.client.connect()
        except Exception as e:
            raise ConnectionError(f'Failed to connect to Weaviate: {e}') from e

    # ------------------------------------------------------------------
    # Schema / collection lifecycle
    # ------------------------------------------------------------------

    def _create_collection(self, coll_name: str) -> None:
        """Create an MT schema collection, or the standalone meta collection.

        The five MT collections are created multi-tenancy-enabled with
        auto tenant creation/activation. The meta collection (Knowledge_bases)
        is created as a plain (non-multi-tenant) collection.
        """
        create_kwargs = {
            'name': coll_name,
            'vector_config': _build_vector_config(coll_name),
            'properties': _mt_properties(),
        }
        if coll_name != KNOWLEDGE_BASES_META:
            create_kwargs['multi_tenancy_config'] = weaviate.classes.config.Configure.multi_tenancy(
                enabled=True,
                auto_tenant_creation=True,
                auto_tenant_activation=True,
            )
        self.client.collections.create(**create_kwargs)

    def _ensure_collection(self, coll_name: str) -> None:
        """Create the schema collection if absent, handling concurrent races."""
        if not self.client.collections.exists(coll_name):
            try:
                self._create_collection(coll_name)
            except UnexpectedStatusCodeError as e:
                if 'already exists' in str(e):
                    log.debug('Collection %s created by another thread', coll_name)
                else:
                    raise

    def _legacy_class_exists(self, collection_name: str) -> Optional[str]:
        """Return the legacy class name if it exists (and fallback is on), else None."""
        if not self.legacy_fallback:
            return None
        try:
            legacy_class = _legacy_sanitize_collection_name(collection_name)
        except ValueError:
            return None
        if self.client.collections.exists(legacy_class):
            return legacy_class
        return None

    def _queryable(self, coll_name: str, tenant: Optional[str]):
        """Return the queryable collection object (tenant-scoped if applicable).

        Assumes the collection exists. For tenant=None (meta) returns the plain
        collection; otherwise scopes to the native tenant.
        """
        coll = self.client.collections.get(coll_name)
        if tenant is None:
            return coll
        return coll.with_tenant(tenant)

    # ------------------------------------------------------------------
    # Read helpers (written ONCE, used by both MT and legacy-fallback paths)
    # ------------------------------------------------------------------

    @staticmethod
    def _search_objects(
        queryable,
        vectors: List[List[Union[float, int]]],
        limit: int,
    ) -> SearchResult:
        result_ids, result_documents, result_metadatas, result_distances = [], [], [], []

        for vector_embedding in vectors:
            try:
                response = queryable.query.near_vector(
                    near_vector=vector_embedding,
                    limit=limit,
                    return_metadata=weaviate.classes.query.MetadataQuery(distance=True),
                )

                ids = [str(obj.uuid) for obj in response.objects]
                documents = []
                metadatas = []

                for obj in response.objects:
                    properties = dict(obj.properties) if obj.properties else {}
                    documents.append(properties.pop('text', ''))
                    metadatas.append(_make_json_serializable(properties))

                # Weaviate cosine distance, 2 (worst) -> 0 (best). Re-order to 0 -> 1.
                raw_distances = [
                    (obj.metadata.distance if obj.metadata and obj.metadata.distance else 2.0)
                    for obj in response.objects
                ]
                distances = [(2 - dist) / 2 for dist in raw_distances]

                result_ids.append(ids)
                result_documents.append(documents)
                result_metadatas.append(metadatas)
                result_distances.append(distances)
            except Exception:
                result_ids.append([])
                result_documents.append([])
                result_metadatas.append([])
                result_distances.append([])

        return SearchResult(
            **{
                'ids': result_ids,
                'documents': result_documents,
                'metadatas': result_metadatas,
                'distances': result_distances,
            }
        )

    @staticmethod
    def _query_objects(queryable, filter: Dict, limit: Optional[int]) -> Optional[GetResult]:
        weaviate_filter = None
        if filter:
            for key, value in filter.items():
                prop_filter = weaviate.classes.query.Filter.by_property(name=key).equal(value)
                weaviate_filter = (
                    prop_filter
                    if weaviate_filter is None
                    else weaviate.classes.query.Filter.all_of([weaviate_filter, prop_filter])
                )

        try:
            response = queryable.query.fetch_objects(filters=weaviate_filter, limit=limit)

            ids = [str(obj.uuid) for obj in response.objects]
            documents = []
            metadatas = []

            for obj in response.objects:
                properties = dict(obj.properties) if obj.properties else {}
                documents.append(properties.pop('text', ''))
                metadatas.append(_make_json_serializable(properties))

            return GetResult(
                **{
                    'ids': [ids],
                    'documents': [documents],
                    'metadatas': [metadatas],
                }
            )
        except Exception:
            return None

    @staticmethod
    def _get_objects(queryable) -> Optional[GetResult]:
        ids, documents, metadatas = [], [], []
        try:
            for item in queryable.iterator():
                ids.append(str(item.uuid))
                properties = dict(item.properties) if item.properties else {}
                documents.append(properties.pop('text', ''))
                metadatas.append(_make_json_serializable(properties))

            if not ids:
                return None

            return GetResult(
                **{
                    'ids': [ids],
                    'documents': [documents],
                    'metadatas': [metadatas],
                }
            )
        except Exception:
            return None

    @staticmethod
    def _result_is_empty(result: Optional[GetResult]) -> bool:
        """True when a Get/SearchResult carries no documents."""
        if result is None:
            return True
        docs = result.documents or []
        return not any(group for group in docs)

    def _tenant_exists(self, coll_name: str, tenant: Optional[str]) -> bool:
        """True if the MT tenant exists (collection may not exist yet -> False)."""
        if tenant is None:
            return self.client.collections.exists(coll_name)
        if not self.client.collections.exists(coll_name):
            return False
        try:
            return self.client.collections.get(coll_name).tenants.exists(tenant)
        except Exception:
            return False

    # ------------------------------------------------------------------
    # VectorDBBase interface
    # ------------------------------------------------------------------

    def has_collection(self, collection_name: str) -> bool:
        coll_name, tenant = map_collection(collection_name)
        if self._tenant_exists(coll_name, tenant):
            return True
        # Dual-read: also true if the legacy class exists.
        if self._legacy_class_exists(collection_name) is not None:
            return True
        return False

    def insert(self, collection_name: str, items: List[VectorItem]) -> None:
        coll_name, tenant = map_collection(collection_name)
        self._ensure_collection(coll_name)

        collection = self.client.collections.get(coll_name)
        writable = collection if tenant is None else collection.with_tenant(tenant)

        with writable.batch.fixed_size(batch_size=100) as batch:
            for item in items:
                item_uuid = str(uuid.uuid4()) if not item['id'] else str(item['id'])

                properties = {'text': item['text']}
                if item['metadata']:
                    clean_metadata = _sanitize_metadata_keys(
                        _make_json_serializable(process_metadata(item['metadata']))
                    )
                    clean_metadata.pop('text', None)
                    properties.update(clean_metadata)

                batch.add_object(properties=properties, uuid=item_uuid, vector=item['vector'])

    def upsert(self, collection_name: str, items: List[VectorItem]) -> None:
        coll_name, tenant = map_collection(collection_name)
        self._ensure_collection(coll_name)

        collection = self.client.collections.get(coll_name)
        writable = collection if tenant is None else collection.with_tenant(tenant)

        with writable.batch.fixed_size(batch_size=100) as batch:
            for item in items:
                item_uuid = str(item['id']) if item['id'] else None

                properties = {'text': item['text']}
                if item['metadata']:
                    clean_metadata = _sanitize_metadata_keys(
                        _make_json_serializable(process_metadata(item['metadata']))
                    )
                    clean_metadata.pop('text', None)
                    properties.update(clean_metadata)

                batch.add_object(properties=properties, uuid=item_uuid, vector=item['vector'])

    def search(
        self,
        collection_name: str,
        vectors: List[List[Union[float, int]]],
        filter: Optional[dict] = None,
        limit: int = 10,
    ) -> Optional[SearchResult]:
        coll_name, tenant = map_collection(collection_name)

        result = None
        if self._tenant_exists(coll_name, tenant):
            result = self._search_objects(self._queryable(coll_name, tenant), vectors, limit)

        # Dual-read: MT tenant absent or empty -> fall back to legacy class.
        if self._result_is_empty(result):
            legacy_class = self._legacy_class_exists(collection_name)
            if legacy_class is not None:
                return self._search_objects(self.client.collections.get(legacy_class), vectors, limit)

        return result

    def query(self, collection_name: str, filter: Dict, limit: Optional[int] = None) -> Optional[GetResult]:
        coll_name, tenant = map_collection(collection_name)

        result = None
        if self._tenant_exists(coll_name, tenant):
            result = self._query_objects(self._queryable(coll_name, tenant), filter, limit)

        if self._result_is_empty(result):
            legacy_class = self._legacy_class_exists(collection_name)
            if legacy_class is not None:
                return self._query_objects(self.client.collections.get(legacy_class), filter, limit)

        return result

    def get(self, collection_name: str) -> Optional[GetResult]:
        coll_name, tenant = map_collection(collection_name)

        result = None
        if self._tenant_exists(coll_name, tenant):
            result = self._get_objects(self._queryable(coll_name, tenant))

        if self._result_is_empty(result):
            legacy_class = self._legacy_class_exists(collection_name)
            if legacy_class is not None:
                return self._get_objects(self.client.collections.get(legacy_class))

        return result

    @staticmethod
    def _build_delete_filter(filter: Dict):
        weaviate_filter = None
        for key, value in filter.items():
            prop_filter = weaviate.classes.query.Filter.by_property(name=key).equal(value)
            weaviate_filter = (
                prop_filter
                if weaviate_filter is None
                else weaviate.classes.query.Filter.all_of([weaviate_filter, prop_filter])
            )
        return weaviate_filter

    def _apply_delete(self, queryable, ids: Optional[List[str]], filter: Optional[Dict]) -> None:
        try:
            if ids:
                for item_id in ids:
                    queryable.data.delete_by_id(uuid=item_id)
            elif filter:
                weaviate_filter = self._build_delete_filter(filter)
                if weaviate_filter:
                    queryable.data.delete_many(where=weaviate_filter)
        except Exception:
            pass

    def delete(
        self,
        collection_name: str,
        ids: Optional[List[str]] = None,
        filter: Optional[Dict] = None,
    ) -> None:
        coll_name, tenant = map_collection(collection_name)

        # Tenant-scoped delete on the MT collection (if the tenant exists).
        if self._tenant_exists(coll_name, tenant):
            self._apply_delete(self._queryable(coll_name, tenant), ids, filter)

        # Anti-resurrection: also delete from the legacy class so the dual-read
        # shim cannot resurrect this data during the migration window.
        legacy_class = self._legacy_class_exists(collection_name)
        if legacy_class is not None:
            self._apply_delete(self.client.collections.get(legacy_class), ids, filter)

    def delete_collection(self, collection_name: str) -> None:
        coll_name, tenant = map_collection(collection_name)

        if tenant is None:
            # Standalone meta collection — drop the whole collection (legacy parity).
            try:
                self.client.collections.delete(coll_name)
            except Exception:
                pass
        else:
            # Drop the tenant/shard, NOT the shared schema collection.
            if self.client.collections.exists(coll_name):
                try:
                    self.client.collections.get(coll_name).tenants.remove([tenant])
                except Exception:
                    pass

        # Anti-resurrection: also delete the legacy class so dropped KBs/files
        # don't reappear via dual-read.
        legacy_class = self._legacy_class_exists(collection_name)
        if legacy_class is not None:
            try:
                self.client.collections.delete(legacy_class)
            except Exception:
                pass

    def reset(self) -> None:
        # Wipe everything: the five MT collections, the meta collection, and any
        # remaining legacy classes. Mirrors legacy reset (delete all collections).
        try:
            for coll_name in self.client.collections.list_all().keys():
                self.client.collections.delete(coll_name)
        except Exception:
            pass
