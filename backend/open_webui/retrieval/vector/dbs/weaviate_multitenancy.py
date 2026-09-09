"""
NOTE: This vector database integration is community-supported and maintained on a best-effort basis.

Native Weaviate multi-tenancy connector — the sole Weaviate connector. Routes
every OWUI logical collection into one of five fixed schema collections
(Knowledge, File, WebSearch, UserMemory, HashBased) as a native Weaviate TENANT
(one shard + dedicated vector index per tenant), plus a standalone
non-multi-tenant meta collection (Knowledge_bases).

The legacy per-class connector (``weaviate.py``) and its dual-read fallback shim
were removed after the fleet-wide MT migration completed (2026-07).
"""

import logging
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

import weaviate
from weaviate.exceptions import UnexpectedStatusCodeError

from open_webui.retrieval.vector.main import (
    VectorDBBase,
    VectorItem,
    SearchResult,
    GetResult,
)
from open_webui.retrieval.vector.utils import iter_filter_conditions, process_metadata
from open_webui.retrieval.vector.dbs._weaviate_mt_mapping import (
    map_collection,
    FILE,
    WEB_SEARCH,
    USER_MEMORY,
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
)

log = logging.getLogger(__name__)


def _make_json_serializable(obj: Any) -> Any:
    """Recursively convert non-JSON-serializable objects (UUID, datetime) to strings."""
    if isinstance(obj, uuid.UUID):
        return str(obj)
    elif isinstance(obj, datetime):
        return obj.isoformat()
    elif isinstance(obj, dict):
        return {key: _make_json_serializable(value) for key, value in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return type(obj)(_make_json_serializable(item) for item in obj)
    else:
        return obj


def _sanitize_property_name(name: str) -> str:
    """Sanitize property name to be a valid Weaviate/GraphQL identifier.

    Weaviate property names must match /[_A-Za-z][_0-9A-Za-z]{0,230}/.
    PDF metadata can contain hyphens (e.g. 'pdfsettings-inchmargins') which
    cause silent batch insert failures.
    """
    sanitized = re.sub(r'[^a-zA-Z0-9_]', '_', name)
    sanitized = sanitized.strip('_')
    if not sanitized:
        return None
    if not sanitized[0].isalpha() and sanitized[0] != '_':
        sanitized = '_' + sanitized
    return sanitized


def _sanitize_metadata_keys(metadata: dict) -> dict:
    """Sanitize all metadata keys to be valid Weaviate property names."""
    result = {}
    for key, value in metadata.items():
        sanitized_key = _sanitize_property_name(key)
        if sanitized_key:
            result[sanitized_key] = value
    return result


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


# Canonical property list, applied uniformly to all five MT collections and the
# meta collection: cloud-sync metadata on Knowledge/File requires it and it is
# harmless on the others. Explicitly typed to prevent Weaviate auto-schema from
# inferring wrong types. Auto-schema is off, so any retrievable chunk-metadata
# key MUST be declared here (undeclared keys are silently dropped at insert).
def _mt_properties() -> list:
    return [
        weaviate.classes.config.Property(name='text', data_type=weaviate.classes.config.DataType.TEXT),
        # Core file metadata - always present
        weaviate.classes.config.Property(name='file_id', data_type=weaviate.classes.config.DataType.TEXT),
        # [Gradient] ACL metadata searches and hash-based re-upload/deletion need declared properties.
        weaviate.classes.config.Property(name='knowledge_base_id', data_type=weaviate.classes.config.DataType.TEXT),
        weaviate.classes.config.Property(name='hash', data_type=weaviate.classes.config.DataType.TEXT),
        weaviate.classes.config.Property(name='name', data_type=weaviate.classes.config.DataType.TEXT),
        weaviate.classes.config.Property(name='source', data_type=weaviate.classes.config.DataType.TEXT),
        # Cloud-sync provenance: original page/document URL for citations (PR #195).
        weaviate.classes.config.Property(name='source_url', data_type=weaviate.classes.config.DataType.TEXT),
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
        # Chunk/splitter metadata — declared as NUMBER (float64) to match Weaviate's
        # auto-schema inference and prevent first-writer type races on fresh tenants.
        # OWUI writes these as int; Weaviate stores and returns them as float64.
        weaviate.classes.config.Property(name='start_index', data_type=weaviate.classes.config.DataType.NUMBER),
        weaviate.classes.config.Property(name='page', data_type=weaviate.classes.config.DataType.NUMBER),
        weaviate.classes.config.Property(name='total_pages', data_type=weaviate.classes.config.DataType.NUMBER),
        # Position of the chunk in the document's chunk list, stamped at indexing
        # time (save_docs_to_vector_db). The authoritative document order: it is
        # unrecoverable from `start_index`, which the markdown header splitter
        # resets per section. OWUI writes int; Weaviate stores/returns float64.
        weaviate.classes.config.Property(name='chunk_index', data_type=weaviate.classes.config.DataType.NUMBER),
        # Citation geometry: JSON string of [{page, x0, y0, x1, y1}] rects
        # (PDF points, top-left origin, 0-based pages) serialized with
        # json.dumps in routers/integrations.py. The citation modal parses it
        # to draw bbox highlights on the PDF viewer.
        weaviate.classes.config.Property(name='bboxes', data_type=weaviate.classes.config.DataType.TEXT),
    ]


# [Gradient] Apply upstream metadata-filter semantics within the fixed MT schema.
def _metadata_filter(filter: Optional[dict]) -> Any:
    declared = {prop.name for prop in _mt_properties()}
    normalized = {}
    for key, value in (filter or {}).items():
        if key not in declared:
            raise ValueError(f'Undeclared metadata filter property: {key}')
        # The shared iterator represents equality as a scalar; accept explicit $eq too.
        if isinstance(value, dict) and set(value) == {'$eq'}:
            value = value['$eq']
        # [Gradient] Nullable file hashes must never become valueless delete filters.
        if value is None:
            raise ValueError('Metadata filter values must not be null')
        if isinstance(value, dict) and set(value) == {'$in'}:
            if not isinstance(value['$in'], (list, tuple)) or not value['$in']:
                raise ValueError('Metadata $in requires a non-empty list')
            if any(item is None for item in value['$in']):
                raise ValueError('Metadata $in values must not be null')
        normalized[key] = value

    clauses = []
    for key, op, value in iter_filter_conditions(normalized):
        if op == '$in':
            clauses.append(
                weaviate.classes.query.Filter.any_of(
                    [weaviate.classes.query.Filter.by_property(name=key).equal(item) for item in value]
                )
            )
        else:
            clauses.append(weaviate.classes.query.Filter.by_property(name=key).equal(value))
    return weaviate.classes.query.Filter.all_of(clauses) if len(clauses) > 1 else (clauses[0] if clauses else None)


class WeaviateClient(VectorDBBase):
    def __init__(self):
        self.url = WEAVIATE_HTTP_HOST
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

        # Collections whose declared properties have been verified/added this
        # process. Auto-schema is off (see _create_collection), so every
        # retrievable key (`source_url`, `chunk_index`, `bboxes`, ...) must be
        # an explicit property; this set avoids re-checking the schema on every
        # insert batch.
        self._properties_ensured: set[str] = set()

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
        else:
            # Collection predates a later-declared property: add it in place so a
            # re-index populates it without reprovisioning the collection.
            self._ensure_declared_properties(coll_name)

    def _ensure_declared_properties(self, coll_name: str) -> None:
        """Idempotently add any missing `_mt_properties()` entry to an existing collection.

        Auto-schema is off, so inserts silently drop undeclared properties. New
        collections get the full list from `_mt_properties`; collections created
        before a property was declared (`source_url`, `chunk_index`, `bboxes`)
        need it added once, with its declared type. The schema is
        collection-level (tenant-agnostic), so one call covers all tenants.
        Cached per process.
        """
        if coll_name in self._properties_ensured:
            return
        try:
            collection = self.client.collections.get(coll_name)
            existing = {p.name for p in collection.config.get().properties}
            for prop in _mt_properties():
                if prop.name in existing:
                    continue
                collection.config.add_property(prop)
                log.info('Added %s property to existing collection %s', prop.name, coll_name)
        except Exception as e:
            # Non-fatal: a concurrent add or a transient error must not block the
            # insert. The property either already exists or will be retried next call.
            log.debug('Could not ensure declared properties on %s: %s', coll_name, e)
            return
        self._properties_ensured.add(coll_name)

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
    # Read helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _search_objects(
        queryable,
        vectors: List[List[Union[float, int]]],
        limit: int,
        filters=None,
    ) -> SearchResult:
        result_ids, result_documents, result_metadatas, result_distances = [], [], [], []

        for vector_embedding in vectors:
            try:
                response = queryable.query.near_vector(
                    near_vector=vector_embedding,
                    filters=filters,
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
        weaviate_filter = _metadata_filter(filter)

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
        return self._tenant_exists(coll_name, tenant)

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
        weaviate_filter = _metadata_filter(filter)
        coll_name, tenant = map_collection(collection_name)
        if not self._tenant_exists(coll_name, tenant):
            return None
        return self._search_objects(self._queryable(coll_name, tenant), vectors, limit, filters=weaviate_filter)

    def query(self, collection_name: str, filter: Dict, limit: Optional[int] = None) -> Optional[GetResult]:
        _metadata_filter(filter)  # [Gradient] Reject invalid filters even when the tenant is absent.
        coll_name, tenant = map_collection(collection_name)
        if not self._tenant_exists(coll_name, tenant):
            return None
        return self._query_objects(self._queryable(coll_name, tenant), filter, limit)

    def get(self, collection_name: str) -> Optional[GetResult]:
        coll_name, tenant = map_collection(collection_name)
        if not self._tenant_exists(coll_name, tenant):
            return None
        return self._get_objects(self._queryable(coll_name, tenant))

    @staticmethod
    def _build_delete_filter(filter: Dict):
        return _metadata_filter(filter)

    def _apply_delete(self, queryable, ids: Optional[List[str]], filter: Optional[Dict]) -> None:
        weaviate_filter = self._build_delete_filter(filter)
        try:
            if ids:
                for item_id in ids:
                    queryable.data.delete_by_id(uuid=item_id)
            elif filter:
                if weaviate_filter:
                    queryable.data.delete_many(where=weaviate_filter)
        except Exception as e:
            log.warning('Weaviate MT delete failed: %s', e)

    def delete(
        self,
        collection_name: str,
        ids: Optional[List[str]] = None,
        filter: Optional[Dict] = None,
    ) -> None:
        _metadata_filter(filter)  # [Gradient] Validation failures must escape the best-effort delete path.
        coll_name, tenant = map_collection(collection_name)

        # Tenant-scoped delete on the MT collection (if the tenant exists).
        if self._tenant_exists(coll_name, tenant):
            self._apply_delete(self._queryable(coll_name, tenant), ids, filter)

    def delete_collection(self, collection_name: str) -> None:
        coll_name, tenant = map_collection(collection_name)

        if tenant is None:
            # Standalone meta collection — drop the whole collection.
            try:
                self.client.collections.delete(coll_name)
            except Exception:
                pass
        else:
            # Drop the tenant/shard, NOT the shared schema collection.
            if self.client.collections.exists(coll_name):
                try:
                    self.client.collections.get(coll_name).tenants.remove([tenant])
                except Exception as e:
                    log.warning('Weaviate MT tenants.remove failed: %s', e)

    def reset(self) -> None:
        # Wipe everything: the five MT collections, the meta collection, and any
        # other remaining classes (delete all collections).
        try:
            for coll_name in self.client.collections.list_all().keys():
                self.client.collections.delete(coll_name)
        except Exception:
            pass
