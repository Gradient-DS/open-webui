import logging
import weaviate
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

from open_webui.retrieval.vector.main import (
    VectorDBBase,
    VectorItem,
    SearchResult,
    GetResult,
)
from open_webui.retrieval.vector.utils import process_metadata
from weaviate.exceptions import UnexpectedStatusCodeError
from open_webui.config import (
    WEAVIATE_HTTP_HOST,
    WEAVIATE_HTTP_PORT,
    WEAVIATE_GRPC_PORT,
    WEAVIATE_API_KEY,
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


class WeaviateClient(VectorDBBase):
    def __init__(self):
        self.url = WEAVIATE_HTTP_HOST
        try:
            # Build connection parameters
            connection_params = {
                "host": WEAVIATE_HTTP_HOST,
                "port": WEAVIATE_HTTP_PORT,
                "grpc_port": WEAVIATE_GRPC_PORT,
            }

            # Only add auth_credentials if WEAVIATE_API_KEY exists and is not empty
            if WEAVIATE_API_KEY:
                connection_params["auth_credentials"] = (
                    weaviate.classes.init.Auth.api_key(WEAVIATE_API_KEY)
                )

            self.client = weaviate.connect_to_local(**connection_params)
            self.client.connect()
        except Exception as e:
            raise ConnectionError(f"Failed to connect to Weaviate: {e}") from e

    def _sanitize_collection_name(self, collection_name: str) -> str:
        """Sanitize collection name to be a valid Weaviate class name."""
        if not isinstance(collection_name, str) or not collection_name.strip():
            raise ValueError("Collection name must be a non-empty string")

        # Requirements for a valid Weaviate class name:
        # The collection name must begin with a capital letter.
        # The name can only contain letters, numbers, and the underscore (_) character. Spaces are not allowed.

        # Replace hyphens with underscores and keep only alphanumeric characters
        name = re.sub(r"[^a-zA-Z0-9_]", "", collection_name.replace("-", "_"))
        name = name.strip("_")

        if not name:
            raise ValueError(
                "Could not sanitize collection name to be a valid Weaviate class name"
            )

        # Ensure it starts with a letter and is capitalized
        if not name[0].isalpha():
            name = "C" + name

        return name[0].upper() + name[1:]

    def has_collection(self, collection_name: str) -> bool:
        sane_collection_name = self._sanitize_collection_name(collection_name)
        return self.client.collections.exists(sane_collection_name)

    def delete_collection(self, collection_name: str) -> None:
        sane_collection_name = self._sanitize_collection_name(collection_name)
        try:
            self.client.collections.delete(sane_collection_name)
        except Exception:
            pass  # Collection may not exist, that's fine

    def _create_collection(self, collection_name: str) -> None:
        # Explicitly define all expected properties as TEXT to prevent Weaviate auto-schema
        # from inferring incorrect types (e.g., UUID for file_id, DATE for moddate).
        # This fixes conflicts when mixing file sources (OneDrive, PDF uploads, etc.)
        self.client.collections.create(
            name=collection_name,
            vector_config=weaviate.classes.config.Configure.Vectors.self_provided(),
            properties=[
                weaviate.classes.config.Property(
                    name="text", data_type=weaviate.classes.config.DataType.TEXT
                ),
                # Core file metadata - always present
                weaviate.classes.config.Property(
                    name="file_id", data_type=weaviate.classes.config.DataType.TEXT
                ),
                weaviate.classes.config.Property(
                    name="name", data_type=weaviate.classes.config.DataType.TEXT
                ),
                weaviate.classes.config.Property(
                    name="source", data_type=weaviate.classes.config.DataType.TEXT
                ),
                weaviate.classes.config.Property(
                    name="created_by", data_type=weaviate.classes.config.DataType.TEXT
                ),
                # PDF metadata - dates come in non-RFC3339 format
                weaviate.classes.config.Property(
                    name="moddate", data_type=weaviate.classes.config.DataType.TEXT
                ),
                weaviate.classes.config.Property(
                    name="creationdate", data_type=weaviate.classes.config.DataType.TEXT
                ),
                # OneDrive metadata
                weaviate.classes.config.Property(
                    name="onedrive_item_id", data_type=weaviate.classes.config.DataType.TEXT
                ),
                weaviate.classes.config.Property(
                    name="onedrive_drive_id", data_type=weaviate.classes.config.DataType.TEXT
                ),
            ],
        )

    def _ensure_collection(self, sane_collection_name: str) -> None:
        """Create collection if it doesn't exist, handling concurrent creation races."""
        if not self.client.collections.exists(sane_collection_name):
            try:
                self._create_collection(sane_collection_name)
            except UnexpectedStatusCodeError as e:
                if "already exists" in str(e):
                    log.debug("Collection %s created by another thread", sane_collection_name)
                else:
                    raise

    def insert(self, collection_name: str, items: List[VectorItem]) -> None:
        sane_collection_name = self._sanitize_collection_name(collection_name)
        self._ensure_collection(sane_collection_name)

        collection = self.client.collections.get(sane_collection_name)

        with collection.batch.fixed_size(batch_size=100) as batch:
            for item in items:
                item_uuid = str(uuid.uuid4()) if not item["id"] else str(item["id"])

                properties = {"text": item["text"]}
                if item["metadata"]:
                    clean_metadata = _make_json_serializable(
                        process_metadata(item["metadata"])
                    )
                    clean_metadata.pop("text", None)
                    properties.update(clean_metadata)

                batch.add_object(
                    properties=properties, uuid=item_uuid, vector=item["vector"]
                )

    def upsert(self, collection_name: str, items: List[VectorItem]) -> None:
        sane_collection_name = self._sanitize_collection_name(collection_name)
        self._ensure_collection(sane_collection_name)

        collection = self.client.collections.get(sane_collection_name)

        with collection.batch.fixed_size(batch_size=100) as batch:
            for item in items:
                item_uuid = str(item["id"]) if item["id"] else None

                properties = {"text": item["text"]}
                if item["metadata"]:
                    clean_metadata = _make_json_serializable(
                        process_metadata(item["metadata"])
                    )
                    clean_metadata.pop("text", None)
                    properties.update(clean_metadata)

                batch.add_object(
                    properties=properties, uuid=item_uuid, vector=item["vector"]
                )

    def search(
        self, collection_name: str, vectors: List[List[Union[float, int]]], limit: int
    ) -> Optional[SearchResult]:
        sane_collection_name = self._sanitize_collection_name(collection_name)
        if not self.client.collections.exists(sane_collection_name):
            return None

        collection = self.client.collections.get(sane_collection_name)

        result_ids, result_documents, result_metadatas, result_distances = (
            [],
            [],
            [],
            [],
        )

        for vector_embedding in vectors:
            try:
                response = collection.query.near_vector(
                    near_vector=vector_embedding,
                    limit=limit,
                    return_metadata=weaviate.classes.query.MetadataQuery(distance=True),
                )

                ids = [str(obj.uuid) for obj in response.objects]
                documents = []
                metadatas = []
                distances = []

                for obj in response.objects:
                    properties = dict(obj.properties) if obj.properties else {}
                    documents.append(properties.pop("text", ""))
                    metadatas.append(_make_json_serializable(properties))

                # Weaviate has cosine distance, 2 (worst) -> 0 (best). Re-ordering to 0 -> 1
                raw_distances = [
                    (
                        obj.metadata.distance
                        if obj.metadata and obj.metadata.distance
                        else 2.0
                    )
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
                "ids": result_ids,
                "documents": result_documents,
                "metadatas": result_metadatas,
                "distances": result_distances,
            }
        )

    def query(
        self, collection_name: str, filter: Dict, limit: Optional[int] = None
    ) -> Optional[GetResult]:
        sane_collection_name = self._sanitize_collection_name(collection_name)
        if not self.client.collections.exists(sane_collection_name):
            return None

        collection = self.client.collections.get(sane_collection_name)

        weaviate_filter = None
        if filter:
            for key, value in filter.items():
                prop_filter = weaviate.classes.query.Filter.by_property(name=key).equal(
                    value
                )
                weaviate_filter = (
                    prop_filter
                    if weaviate_filter is None
                    else weaviate.classes.query.Filter.all_of(
                        [weaviate_filter, prop_filter]
                    )
                )

        try:
            response = collection.query.fetch_objects(
                filters=weaviate_filter, limit=limit
            )

            ids = [str(obj.uuid) for obj in response.objects]
            documents = []
            metadatas = []

            for obj in response.objects:
                properties = dict(obj.properties) if obj.properties else {}
                documents.append(properties.pop("text", ""))
                metadatas.append(_make_json_serializable(properties))

            return GetResult(
                **{
                    "ids": [ids],
                    "documents": [documents],
                    "metadatas": [metadatas],
                }
            )
        except Exception:
            return None

    def get(self, collection_name: str) -> Optional[GetResult]:
        sane_collection_name = self._sanitize_collection_name(collection_name)
        if not self.client.collections.exists(sane_collection_name):
            return None

        collection = self.client.collections.get(sane_collection_name)
        ids, documents, metadatas = [], [], []

        try:
            for item in collection.iterator():
                ids.append(str(item.uuid))
                properties = dict(item.properties) if item.properties else {}
                documents.append(properties.pop("text", ""))
                metadatas.append(_make_json_serializable(properties))

            if not ids:
                return None

            return GetResult(
                **{
                    "ids": [ids],
                    "documents": [documents],
                    "metadatas": [metadatas],
                }
            )
        except Exception:
            return None

    def delete(
        self,
        collection_name: str,
        ids: Optional[List[str]] = None,
        filter: Optional[Dict] = None,
    ) -> None:
        sane_collection_name = self._sanitize_collection_name(collection_name)
        if not self.client.collections.exists(sane_collection_name):
            return

        collection = self.client.collections.get(sane_collection_name)

        try:
            if ids:
                for item_id in ids:
                    collection.data.delete_by_id(uuid=item_id)
            elif filter:
                weaviate_filter = None
                for key, value in filter.items():
                    prop_filter = weaviate.classes.query.Filter.by_property(
                        name=key
                    ).equal(value)
                    weaviate_filter = (
                        prop_filter
                        if weaviate_filter is None
                        else weaviate.classes.query.Filter.all_of(
                            [weaviate_filter, prop_filter]
                        )
                    )

                if weaviate_filter:
                    collection.data.delete_many(where=weaviate_filter)
        except Exception:
            pass

    def reset(self) -> None:
        try:
            for collection_name in self.client.collections.list_all().keys():
                self.client.collections.delete(collection_name)
        except Exception:
            pass
