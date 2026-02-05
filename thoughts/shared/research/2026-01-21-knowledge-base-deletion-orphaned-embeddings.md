---
date: 2026-01-21T20:00:00+01:00
researcher: Claude
git_commit: 0efff3fcc536232c4ad3bbb7a93773296bf8908f
branch: main
repository: open-webui
topic: "Knowledge base deletion does not delete individual file embeddings"
tags: [research, codebase, knowledge, vector-db, weaviate, onedrive, bug]
status: complete
last_updated: 2026-01-21
last_updated_by: Claude
---

# Research: Knowledge Base Deletion Does Not Delete Individual File Embeddings

**Date**: 2026-01-21T20:00:00+01:00
**Researcher**: Claude
**Git Commit**: 0efff3fcc536232c4ad3bbb7a93773296bf8908f
**Branch**: main
**Repository**: open-webui

## Research Question

When deleting a knowledge base, the embedded files (individual file collections) are not being deleted from the vector database. Recreating a knowledge base with the same files shows the old embeddings still exist ("Document with hash ... already exists in collection file-onedrive-..., skipping"). Additionally, a Weaviate UUID validation error occurs for OneDrive files.

## Summary

**Two bugs were identified:**

1. **Orphaned File Collections (Primary Bug)**: When a knowledge base is deleted via `delete_knowledge_by_id`, only the main knowledge base collection (`C{knowledge_id}`) is deleted from Weaviate. Individual file collections (`File_{file_id}`) remain orphaned in the vector database. This is because the deletion function does not iterate through associated files and clean them up.

2. **Weaviate UUID Type Mismatch (Secondary Bug)**: OneDrive files use non-UUID file IDs (`onedrive-{item_id}`), but Weaviate auto-infers the `file_id` property as UUID type when the first regular file (with a valid UUID) is inserted. Subsequent OneDrive files fail validation.

## Detailed Findings

### Issue 1: Orphaned File Collections

#### Root Cause

The `delete_knowledge_by_id` function at `backend/open_webui/routers/knowledge.py:629-688` only performs a single vector collection deletion:

```python
# Line 681-686
try:
    VECTOR_DB_CLIENT.delete_collection(collection_name=id)
except Exception as e:
    log.debug(e)
    pass
```

This deletes the main collection (named by the knowledge base ID, e.g., `C39293710_bcf2_4c98_9afe_7eb52073f0a3`) but does NOT:
- Iterate through files associated with the knowledge base
- Delete individual file collections (`File_{file_id}`)
- Delete the File database records

#### Collection Architecture

Open WebUI uses a dual-collection architecture:

| Collection Type | Naming Pattern | When Created | When Deleted |
|----------------|----------------|--------------|--------------|
| Knowledge Base | `{knowledge_id}` (raw UUID) | When first file added to KB | When KB deleted |
| Individual File | `file-{file_id}` | When file first processed | Only when file explicitly removed with `delete_file=True` |

In Weaviate, these become sanitized names:
- `39293710-bcf2-4c98-9afe-7eb52073f0a3` → `C39293710_bcf2_4c98_9afe_7eb52073f0a3`
- `file-onedrive-01HJQG7FZBCEOE7KSHTVA3U6G5LYLPBHC5` → `File_onedrive_01HJQG7FZBCEOE7KSHTVA3U6G5LYLPBHC5`

#### Data Flow

```
DELETE /api/v1/knowledge/{id}/delete
           │
           ▼
┌──────────────────────────────────┐
│ delete_knowledge_by_id()         │
│ knowledge.py:629                 │
└──────────────────────────────────┘
           │
           ├─────────────────────────────────────────────────┐
           │                                                 │
           ▼                                                 ▼
┌──────────────────────────────────┐   ┌────────────────────────────────┐
│ VECTOR_DB_CLIENT.delete_collection│   │ NOT DELETED:                   │
│ collection_name=id               │   │ - file-{file_id} collections   │
│ knowledge.py:683                 │   │ - File database records        │
│ (Only deletes C{uuid} collection)│   │ - Storage files                │
└──────────────────────────────────┘   └────────────────────────────────┘
```

#### Contrast: File Removal Endpoint Has Proper Cleanup

The `remove_file_from_knowledge_by_id` endpoint at `knowledge.py:548-621` correctly handles individual file cleanup:

```python
# Lines 598-610
if delete_file:
    file_collection = f"file-{form_data.file_id}"
    if VECTOR_DB_CLIENT.has_collection(collection_name=file_collection):
        VECTOR_DB_CLIENT.delete_collection(collection_name=file_collection)
    Files.delete_file_by_id(form_data.file_id)
```

This logic is missing from `delete_knowledge_by_id`.

#### Available Methods to Fix

The `Knowledges` model provides methods to iterate through files:

```python
# backend/open_webui/models/knowledge.py:473-484
def get_files_by_id(self, knowledge_id: str) -> list[FileModel]:
    with get_db() as db:
        files = (
            db.query(File)
            .join(KnowledgeFile, File.id == KnowledgeFile.file_id)
            .filter(KnowledgeFile.knowledge_id == knowledge_id)
            .all()
        )
        return [FileModel.model_validate(file) for file in files]
```

### Issue 2: Weaviate UUID Type Mismatch

#### Root Cause

1. Regular files use UUID format for `file_id`:
   ```python
   # backend/open_webui/routers/files.py:219
   id = str(uuid.uuid4())  # e.g., "1de8c988-9d55-41d7-ae06-e390f15101fb"
   ```

2. OneDrive files use non-UUID format:
   ```python
   # backend/open_webui/services/onedrive/sync_worker.py:597
   file_id = f"onedrive-{item_id}"  # e.g., "onedrive-01HJQG7FZBCEOE7KSHTVA3U6G5LYLPBHC5"
   ```

3. Weaviate collection creation only defines `text` property:
   ```python
   # backend/open_webui/retrieval/vector/dbs/weaviate.py:91-100
   properties=[
       weaviate.classes.config.Property(
           name="text", data_type=weaviate.classes.config.DataType.TEXT
       ),
   ]
   ```

4. When metadata is inserted, Weaviate auto-infers property types:
   - First regular file: `file_id` → inferred as `DataType.UUID`
   - Later OneDrive file: `file_id = "onedrive-..."` → **fails UUID validation**

#### Error Message

```
WeaviateInsertManyAllFailedError("Every object failed during insertion.
Here is the set of all errors: invalid uuid property 'file_id' on class
'C1de8c988_9d55_41d7_ae06_e390f15101fb': requires a string of UUID format,
but the given value is 'onedrive-01HJQG7FZBCEOE7KSHTVA3U6G5LYLPBHC5'")
```

## Code References

- `backend/open_webui/routers/knowledge.py:629-688` - `delete_knowledge_by_id()` endpoint
- `backend/open_webui/routers/knowledge.py:683` - Only deletes main KB collection
- `backend/open_webui/routers/knowledge.py:548-621` - `remove_file_from_knowledge_by_id()` with proper cleanup
- `backend/open_webui/routers/knowledge.py:598-610` - File collection cleanup logic (model for fix)
- `backend/open_webui/models/knowledge.py:473-484` - `get_files_by_id()` method
- `backend/open_webui/models/knowledge.py:604-611` - `delete_knowledge_by_id()` database method
- `backend/open_webui/models/knowledge.py:90-108` - `KnowledgeFile` junction table with CASCADE
- `backend/open_webui/routers/retrieval.py:1382-1396` - Hash deduplication (shows orphaned data)
- `backend/open_webui/routers/retrieval.py:1587-1588` - File collection naming pattern
- `backend/open_webui/retrieval/vector/dbs/weaviate.py:91-100` - Collection creation (missing schema)
- `backend/open_webui/retrieval/vector/dbs/weaviate.py:119` - Metadata insertion as properties
- `backend/open_webui/services/onedrive/sync_worker.py:597` - OneDrive file ID format

## Architecture Insights

### Dual Collection Pattern

Open WebUI stores embeddings in two places:
1. **Individual file collection** (`file-{file_id}`): Created when file is first processed
2. **Knowledge base collection** (`{knowledge_id}`): Aggregates embeddings from all files in KB

When adding a file to a KB, existing embeddings are queried from the file collection and copied:

```python
# retrieval.py:1621-1632
result = VECTOR_DB_CLIENT.query(
    collection_name=f"file-{file.id}", filter={"file_id": file.id}
)
# ... copy docs to knowledge base collection
```

### Database Cascade vs Vector DB Cleanup

- **Database**: `KnowledgeFile` records CASCADE delete when `Knowledge` is deleted
- **Vector DB**: Collections must be explicitly deleted - no cascade relationship

## Recommended Fixes

### Fix 1: Delete File Collections on KB Deletion

Modify `delete_knowledge_by_id` to iterate through files and delete their collections:

```python
# In delete_knowledge_by_id, before deleting the KB record:

# Get all files associated with this knowledge base
files = Knowledges.get_files_by_id(id)

# Delete each file's individual collection
for file in files:
    try:
        file_collection = f"file-{file.id}"
        if VECTOR_DB_CLIENT.has_collection(collection_name=file_collection):
            VECTOR_DB_CLIENT.delete_collection(collection_name=file_collection)
    except Exception as e:
        log.debug(f"Failed to delete file collection {file_collection}: {e}")

# Optionally delete file records (consider adding a parameter)
# for file in files:
#     Files.delete_file_by_id(file.id)

# Then continue with existing KB deletion
VECTOR_DB_CLIENT.delete_collection(collection_name=id)
```

### Fix 2: Define `file_id` as TEXT in Weaviate Schema

Modify collection creation to explicitly define `file_id` as TEXT:

```python
# In backend/open_webui/retrieval/vector/dbs/weaviate.py:_create_collection
properties=[
    weaviate.classes.config.Property(
        name="text", data_type=weaviate.classes.config.DataType.TEXT
    ),
    weaviate.classes.config.Property(
        name="file_id", data_type=weaviate.classes.config.DataType.TEXT
    ),
    # Consider adding other commonly used metadata fields
]
```

## Open Questions

1. Should deleting a KB also delete the associated File database records, or just the vector collections?
2. Should there be a `delete_files` parameter (like in `remove_file_from_knowledge_by_id`) to control this behavior?
3. For existing deployments, how should orphaned collections be cleaned up?
4. Should all metadata properties be explicitly defined in the Weaviate schema to prevent type inference issues?

## Related Research

- `thoughts/shared/research/2026-01-18-onedrive-implementation-best-practices-review.md` - OneDrive integration review
