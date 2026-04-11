# RAG Filter: Open WebUI → agents_api Handoff

## Status

Open WebUI now forwards `rag_filter` to the agents_api. The agents_api does not yet consume it.

## How it works in Open WebUI

### Frontend

Marijn's filter UI (branch `feat/docfilter`) lets users select collections, content subtypes, and specific documents.

**Key files:**
- `src/lib/components/chat/RagFilter.svelte` — filter UI component
- `src/lib/components/chat/RagFilterPanel.svelte` — sliding side panel
- `src/lib/stores/rag-filter.ts` — Svelte store with filter state types
- `src/lib/utils/rag-filter.ts` — `getRagFilterForRequest()` formats for request
- `src/lib/apis/rag/index.ts` — `getCollectionsAndDocuments()` fetches from RAG discovery API

The filter is sent as `rag_filter` in the chat completion request body (`Chat.svelte:1952`).

### Backend

1. `main.py:1765` — Extracts `rag_filter` from `form_data` into `metadata`
2. `utils/agent.py` — `AgentPayload` includes `rag_filter`, `call_agent_api()` passes `metadata.get("rag_filter")` through
3. Feature flag: `ENABLE_RAG_FILTER_UI` controls visibility

### JSON shape sent to agents_api

```json
{
  "rag_filter": {
    "collection_key": {
      "all": true
    },
    "another_collection": {
      "subtypes": {
        "Regelgeving": true,
        "Handleidingen": {
          "doc_ids": [1, 2, "abc-123"],
          "doc_titles": ["Document A", "Document B"]
        }
      }
    }
  }
}
```

**Cases:**
- `{ "all": true }` — entire collection selected, no filtering needed
- `{ "subtypes": { "X": true } }` — entire subtype selected → filter by `contentsubtype_exact`
- `{ "subtypes": { "X": { "doc_ids": [...] } } }` — specific docs → filter by `original_doc_id_in`
- Collection absent from `rag_filter` — collection should be **excluded** from search
- `rag_filter` is `null`/absent — no filtering, search everything normally

## How it was done in the old pipe system

Marijn wrote a complete filter module in `genai-utils/agents/src/agents/rag_filter.py`. This is the reference implementation to port.

### Key functions (all in `rag_filter.py`):

**`parse_rag_filter(raw_filter) -> ParsedRAGFilter`**
Parses the frontend JSON into structured `CollectionSelection` / `SubtypeSelection` dataclasses.

**`build_api_filters_for_collection(collection_selection) -> dict | None`**
Converts a collection's selection into API-compatible filters:
- Specific doc_ids → `{"original_doc_id_in": [...]}`
- Specific doc_id (single) → `{"original_doc_id": id}`
- Full subtype(s) selected → `{"contentsubtype_exact": "name"}` or `{"contentsubtype_exact": ["a", "b"]}`
- Full collection → `None` (no filter)

**`convert_to_agent_filter_format(parsed_filter) -> dict[str, dict]`**
Maps `collection_key → {"api_filters": {...}, "select_all": bool, "subtypes": [...]}`

**`get_collections_to_search(parsed_filter, default_collections) -> list[str]`**
Returns only the collection keys present in the filter (restricts search scope).

**`build_search_filters(collection_key, filter_config) -> dict | None`**
Quick lookup: given a collection key and the converted filter config, returns the API filter dict.

### How the pipe consumed it (`pipes/factory.py:269-274`):

```python
filter_config = _extract_filter_from_metadata(body, __metadata__)
if filter_config:
    config_overrides = {"filter_by_collection": filter_config}
config = self._valves_to_config(config_overrides)
```

The multiagent agent stored `filter_by_collection: Optional[dict[str, dict]]` in its graph state and applied filters per-collection during search.

## What agents_api (genai-utils/agents_updated) needs to do

### 1. Accept `rag_filter` in request

`service/routes/chat.py` — Add to `ChatCompletionRequest`:
```python
rag_filter: dict[str, Any] | None = None
```

Pass to `runner.run(rag_filter=body.rag_filter)`.

### 2. Thread through runner to ProjectConfig

`core/config.py` — Add to `ProjectConfig`:
```python
rag_filter: dict[str, Any] = {}
```

`agents/runner.py` — In `run()`, inject like `openwebui_collections` (line 207-209):
```python
if rag_filter:
    project_config = project_config.model_copy(
        update={"rag_filter": rag_filter},
    )
```

### 3. Port filter parsing

Create `agents_updated/agents/retrieval/filters.py` — port from `genai-utils/agents/src/agents/rag_filter.py`.

The existing `search_source()` and `title_guided_search()` already accept `filters: dict[str, Any] | None`. The ported code just needs to produce those dicts.

### 4. Apply in agent search nodes

Each agent reads `self.config.rag_filter`, parses it once, then for each collection search:
1. Check if collection is in the filter (if not, skip it)
2. Get API filters via `build_api_filters_for_collection()`
3. Pass to `search_source(filters=...)` or `title_guided_search(filters=...)`

Consider adding a helper to `BaseAgent` or `search.py` so agents don't reimplement this.

### Affected agents

- `agents/flows/test/simple_rag.py`
- `agents/flows/projects/neo_nl/v2/agent.py`
- `agents/flows/projects/ez/mkbot_v1/agent.py`

### Filter keys for Weaviate (HttpRetrievalProvider)

The `HttpRetrievalProvider` passes `filters` directly in the POST body to the Flask+Weaviate search API. The supported filter keys are:
- `original_doc_id_in: list` — filter by document IDs
- `original_doc_id: str|int` — filter by single document ID
- `contentsubtype_exact: str|list` — filter by content subtype(s)
