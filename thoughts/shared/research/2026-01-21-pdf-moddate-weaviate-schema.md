---
date: 2026-01-21T13:00:00+01:00
researcher: Claude
git_commit: 0efff3fcc536232c4ad3bbb7a93773296bf8908f
branch: main
repository: open-webui
topic: "Weaviate auto-schema type conflicts in mixed-source knowledge bases"
tags: [research, codebase, weaviate, pdf, onedrive, metadata, rag, knowledge-base, auto-schema]
status: complete
last_updated: 2026-01-21
last_updated_by: Claude
last_updated_note: "Added file_id UUID error and bidirectional failure analysis"
---

# Research: Weaviate auto-schema type conflicts in mixed-source knowledge bases

**Date**: 2026-01-21T13:00:00+01:00
**Researcher**: Claude
**Git Commit**: 0efff3fcc536232c4ad3bbb7a93773296bf8908f
**Branch**: main
**Repository**: open-webui

## Research Question
Why do mixed OneDrive and PDF knowledge bases fail regardless of upload order? Two errors observed:

**Error 1 - OneDrive first, then PDF:**
```
invalid date property 'moddate' on class 'C39a20ed5_...':
requires a string with a RFC3339 formatted date, but the given value is 'D:20230329115553Z00'00''
```

**Error 2 - PDF first, then OneDrive:**
```
invalid uuid property 'file_id' on class 'C1de8c988_...':
requires a string of UUID format, but the given value is 'onedrive-01HJQG7FZBCEOE7KSHTVA3U6G5LYLPBHC5'
```

## Summary

**Root Cause**: Weaviate's auto-schema feature infers data types from the first values it sees. **The problem is bidirectional:**

1. **OneDrive first → PDF fails**: `file_id` gets schema'd as TEXT (OneDrive IDs like `onedrive-xxx`), but later a PDF may trigger `moddate` schema'd as DATE from other sources
2. **PDF first → OneDrive fails**: `file_id` gets schema'd as UUID (PDF file IDs are UUIDs), OneDrive's `onedrive-xxx` format fails

**Key Finding**: The codebase does NOT normalize metadata formats. Both PDF metadata (dates, etc.) and file IDs vary in format depending on source.

**External pipeline vs local**: Document *parsing* may happen on Gradient gateway, but **metadata is added locally** in `retrieval.py` when documents are inserted into Weaviate. The schema conflict happens at insertion time, not parsing time.

**Not related to hybrid RAG**: The `ENABLE_RAG_HYBRID_SEARCH` flag affects retrieval, not document insertion.

## Detailed Findings

### 1. PDF Metadata Flow

**Source**: PyPDFLoader (LangChain) extracts PDF metadata including `/ModDate` as `moddate`

**PDF Date Format**: `D:20230329115553Z00'00'` (PDF specification format)

**Expected Format**: RFC3339 (`2023-03-29T11:55:53Z`)

**Processing Pipeline**:
```
PyPDFLoader.load()
    ↓
retrieval.py:1731-1747 - filter_metadata() applied
    ↓
save_docs_to_vector_db()
    ↓
process_metadata() - only converts datetime objects, not strings
    ↓
_make_json_serializable() - only converts datetime objects, not strings
    ↓
Weaviate batch.add_object() - receives raw PDF date string
```

**Critical Gap** (`backend/open_webui/retrieval/vector/utils.py:13-28`):
```python
def process_metadata(metadata: dict[str, any]) -> dict[str, any]:
    for key, value in metadata.items():
        # Only converts Python datetime objects to strings
        # PDF date strings like 'D:20230329...' pass through unchanged
        if isinstance(value, datetime):
            metadata[key] = str(value)
    return metadata
```

### 2. Weaviate Schema Behavior

**Collection Creation** (`backend/open_webui/retrieval/vector/dbs/weaviate.py:91-100`):
```python
def _create_collection(self, collection_name: str) -> None:
    self.client.collections.create(
        name=collection_name,
        properties=[
            Property(name="text", data_type=DataType.TEXT),
            # NO explicit moddate property defined
        ],
    )
```

**Auto-Schema Behavior**:
- Weaviate automatically detects data types for properties not in the schema
- When inserting `{"moddate": "2024-01-15T10:30:00Z"}`, Weaviate may infer DATE type
- When inserting `{"moddate": "D:20230329115553Z00'00'"}`, Weaviate may infer TEXT type
- **The first value determines the schema for that property**

### 3. OneDrive vs PDF Metadata Comparison

| Property | OneDrive Files | PDF Files | Weaviate Auto-Schema |
|----------|---------------|-----------|---------------------|
| `file_id` | `onedrive-01HJQG7F...` (string) | `68f200ec-1138-456f-...` (UUID) | **CONFLICT** - UUID vs TEXT |
| `moddate` | NOT PRESENT | `D:20230329115553Z00'00'` | **CONFLICT** - DATE vs TEXT |
| `last_synced_at` | Unix timestamp (int) | NOT PRESENT | INT |
| `source` | `"onedrive"` | Filename | TEXT |
| `onedrive_item_id` | `01HJQG7F...` | NOT PRESENT | TEXT |

**OneDrive metadata** (`services/onedrive/sync_worker.py:655-662`):
```python
meta={
    "name": name,
    "content_type": self._get_content_type(name),
    "size": len(content),
    "source": "onedrive",
    "onedrive_item_id": item_id,
    "onedrive_drive_id": drive_id,
    "last_synced_at": int(time.time()),  # Unix timestamp
}
```

**OneDrive file_id format** (`services/onedrive/sync_worker.py:597`):
```python
file_id = f"onedrive-{item_id}"  # e.g., "onedrive-01HJQG7FZBCEOE7KSHTVA3U6G5LYLPBHC5"
```

**PDF/regular file_id format**: Standard UUID from `Files.insert_new_file()`, e.g., `68f200ec-1138-456f-b075-ae79f2828892`

### 4. Knowledge Base File Addition Flow

**Step 1**: OneDrive file processed → stored in `file-{id}` collection → added to knowledge base
**Step 2**: PDF file processed → stored in `file-{id}` collection → **FAILS** when adding to knowledge base

The failure happens in Step 2 because:
1. The knowledge base collection was created with OneDrive file metadata
2. Weaviate auto-schema'd properties based on OneDrive data
3. If OneDrive has a property that looks like a date (or if some other file in the collection has RFC3339 dates), `moddate` is schema'd as DATE
4. PDF's `D:20230329...` format doesn't match RFC3339, causing the error

**Code path** (`routers/retrieval.py:1617-1632`):
```python
elif form_data.collection_name:
    # Fetch existing docs from file's individual collection
    result = VECTOR_DB_CLIENT.query(
        collection_name=f"file-{file.id}", filter={"file_id": file.id}
    )
    # Metadata (including raw PDF dates) is copied directly
    docs = [
        Document(
            page_content=result.documents[0][idx],
            metadata=result.metadatas[0][idx],  # Raw metadata copied
        )
        for idx, id in enumerate(result.ids[0])
    ]
```

### 5. Why Local Works But Cluster Fails

**Possible causes**:
1. **Schema persistence**: Cluster collections persist across deployments; local may use fresh collections
2. **Data history**: Cluster may have old data with RFC3339 dates that established the schema
3. **Processing order**: Local might add PDFs first (establishing TEXT schema) while cluster adds OneDrive first
4. **Weaviate version**: Different auto-schema behavior between versions

**To verify on cluster**:
```bash
# Check the schema for the knowledge base collection
curl -X GET "http://weaviate:8080/v1/schema/C39a20ed5_20e1_4c95_a770_df9ecdecb651"
```

Look for `moddate` property with `dataType: ["date"]` - this confirms the schema issue.

## Code References

- `backend/open_webui/retrieval/vector/dbs/weaviate.py:91-100` - Collection creation (no moddate property)
- `backend/open_webui/retrieval/vector/dbs/weaviate.py:102-123` - Insert method with metadata processing
- `backend/open_webui/retrieval/vector/dbs/weaviate.py:22-33` - `_make_json_serializable()` helper
- `backend/open_webui/retrieval/vector/utils.py:13-28` - `process_metadata()` function
- `backend/open_webui/retrieval/loaders/main.py:362-365` - PyPDFLoader usage
- `backend/open_webui/routers/retrieval.py:1617-1632` - Knowledge base file addition
- `backend/open_webui/routers/retrieval.py:1735-1747` - Document metadata construction
- `backend/open_webui/services/onedrive/sync_worker.py:655-662` - OneDrive metadata

## Recommended Solutions

### Solution 1: Define Explicit Schema for All Properties (Recommended)

Modify `_create_collection()` to explicitly define all expected properties as TEXT:

```python
def _create_collection(self, collection_name: str) -> None:
    self.client.collections.create(
        name=collection_name,
        properties=[
            Property(name="text", data_type=DataType.TEXT),
            # Explicitly define all metadata properties as TEXT to prevent auto-schema conflicts
            Property(name="file_id", data_type=DataType.TEXT),
            Property(name="name", data_type=DataType.TEXT),
            Property(name="source", data_type=DataType.TEXT),
            Property(name="created_by", data_type=DataType.TEXT),
            Property(name="moddate", data_type=DataType.TEXT),
            Property(name="creationDate", data_type=DataType.TEXT),
            Property(name="onedrive_item_id", data_type=DataType.TEXT),
            Property(name="onedrive_drive_id", data_type=DataType.TEXT),
        ],
    )
```

**Pros**: Prevents auto-schema conflicts entirely
**Cons**: Must maintain list of all possible properties; unknown properties still auto-schema'd

### Solution 2: Normalize All Metadata Values to Strings

Add aggressive string conversion in `process_metadata()`:

```python
def process_metadata(metadata: dict[str, any]) -> dict[str, any]:
    for key, value in list(metadata.items()):
        if key in KEYS_TO_EXCLUDE:
            del metadata[key]
        else:
            # Convert EVERYTHING to strings to prevent auto-schema type inference
            metadata[key] = str(value) if value is not None else ""
    return metadata
```

**Pros**: Simple, universal fix
**Cons**: Loses numeric types (might affect filtering/sorting)

### Solution 3: Strip Problematic Metadata Fields

Add all potentially problematic fields to `KEYS_TO_EXCLUDE`:

```python
KEYS_TO_EXCLUDE = [
    "content", "pages", "tables", "paragraphs", "sections", "figures",
    # PDF metadata that may have non-standard formats
    "moddate", "creationDate", "creator", "producer", "author",
    # These are already stored in file record, no need to duplicate
    # "file_id",  # Can't exclude - needed for filtering
]
```

**Pros**: Removes problematic data entirely
**Cons**: Loses potentially useful metadata

### Solution 4: Add PDF Date Parsing

Parse PDF dates to RFC3339 in metadata processing:

```python
import re

def normalize_metadata_value(key: str, value: any) -> any:
    """Normalize metadata values to prevent Weaviate auto-schema conflicts."""
    if isinstance(value, str):
        # Parse PDF date format: D:YYYYMMDDHHmmSS...
        if value.startswith("D:") and len(value) >= 16:
            match = re.match(r"D:(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})", value)
            if match:
                return f"{match[1]}-{match[2]}-{match[3]}T{match[4]}:{match[5]}:{match[6]}Z"
    return value
```

**Pros**: Preserves date information in standard format
**Cons**: Only fixes dates, not file_id issue

### Solution 5: Immediate Workaround

Delete affected knowledge base collections and recreate:

```bash
# Check schema to see what types were inferred
curl -X GET "http://weaviate:8080/v1/schema/C39a20ed5_20e1_4c95_a770_df9ecdecb651"

# Delete collection (will lose data!)
curl -X DELETE "http://weaviate:8080/v1/schema/C39a20ed5_20e1_4c95_a770_df9ecdecb651"

# Re-add files to knowledge base
```

**Note**: This is temporary; the issue will recur with mixed file types.

## Other File Types at Risk

The same auto-schema issues may affect other file types:

| File Type | Loader | Potential Metadata Issues |
|-----------|--------|--------------------------|
| `.docx` | DocxLoader or Tika | `created`, `modified` dates in various formats |
| `.xlsx` | UnstructuredExcelLoader or Tika | Date metadata varies by Office version |
| `.pptx` | UnstructuredPowerPointLoader or Tika | Similar to docx |
| `.html` | BSHTMLLoader | `last-modified` header format varies |
| `.csv` | CSVLoader | No date metadata typically |
| `.txt`, `.md` | TextLoader | No date metadata typically |

**External Pipeline Consideration**: If using `EXTERNAL_PIPELINE_URL` (Gradient gateway), the external service returns parsed content and metadata. However, the metadata is still merged with local metadata (`file_id`, `name`, `source`) in `retrieval.py:1735-1747` before insertion to Weaviate. The schema conflict happens at this insertion point regardless of where parsing occurred.

## Architecture Insights

1. **Weaviate auto-schema is a double-edged sword**: Flexible but can cause type mismatches
2. **Metadata is not normalized**: Different loaders (PDF, OneDrive, Tika) produce different metadata formats
3. **No schema migration**: Changing a property type requires recreating the collection
4. **Two-step file processing**: Files are first stored individually, then copied to knowledge bases with all metadata
5. **External pipeline doesn't solve this**: Parsing location doesn't matter; the issue is metadata format at insertion time

## Open Questions

1. Should ALL metadata values be converted to strings to prevent auto-schema conflicts?
2. Should Weaviate collections explicitly define all expected properties (comprehensive schema)?
3. Is there a Weaviate configuration to disable auto-schema or force TEXT type inference?
4. Should `file_id` format be standardized across all sources (OneDrive, local uploads, etc.)?
5. Should the OneDrive sync worker use UUIDs instead of `onedrive-{item_id}` format?
6. What metadata is actually needed for RAG functionality vs. what's just noise?
