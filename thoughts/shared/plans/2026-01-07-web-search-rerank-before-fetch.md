# Web Search: Rerank SearXNG Results Before Fetch

## Overview

Implement reranking of SearXNG search results **before** fetching full page content. Currently, all URLs from search results are fetched, then chunks are reranked at query time. This wastes time fetching irrelevant pages and pollutes the vector DB with low-quality content.

**Goal**: Use the existing reranking function to score search results by their title+snippet relevance, then only fetch the top-k most relevant URLs.

## Current State Analysis

**Current flow** (`backend/open_webui/routers/retrieval.py:2105-2259`):
```
SearXNG query → Returns ~10 results (title, link, snippet)
      ↓
Collect ALL URLs (no filtering)
      ↓
Fetch ALL pages with Playwright/loader (~10s each)
      ↓
Chunk and embed all content
      ↓
Query-time: Rerank chunks (too late!)
```

**Problem**: If SearXNG returns 10 results and only 3 are relevant, we:
- Waste 70% of fetch time on irrelevant pages
- Pollute the vector DB with noise
- Increase embedding costs unnecessarily

**Key code location** (`retrieval.py:2153-2160`):
```python
for result in search_results:
    if result:
        for item in result:
            if item and item.link:
                result_items.append(item)
                urls.append(item.link)

urls = list(dict.fromkeys(urls))  # Deduplicate - NO RERANKING HERE
```

## Desired End State

**New flow**:
```
SearXNG query → Returns ~10 results (title, link, snippet)
      ↓
Convert to Documents (title + snippet as content)
      ↓
RERANK with existing reranking function
      ↓
Select top-k results (default: 5)
      ↓
Fetch only top-k pages
      ↓
Chunk and embed (less noise)
```

### Verification:
- Web search returns results from only the top-k reranked URLs
- Lower-relevance URLs are not fetched (check logs)
- Total fetch time reduced proportionally
- Search quality improved (more relevant context)

## What We're NOT Doing

- Adding new reranking models (use existing `RERANKING_FUNCTION`)
- Changing the query-time chunk reranking (keep both stages)
- Adding score threshold filtering (just top-k selection)
- Modifying SearXNG configuration
- Adding external API dependencies

## Implementation Approach

1. Add new config option `WEB_SEARCH_RERANK_TOP_K`
2. After collecting search results, convert to Documents
3. Call existing reranking function with combined query
4. Sort by score, select top-k
5. Only pass those URLs to the web loader

---

## Phase 1: Add Configuration Option

### Overview
Add a new config option to control how many URLs to keep after reranking.

### Changes Required:

#### 1. Add config variable
**File**: `backend/open_webui/config.py`
**Location**: After `WEB_SEARCH_RESULT_COUNT` (around line 3020)

```python
WEB_SEARCH_RERANK_TOP_K = PersistentConfig(
    "WEB_SEARCH_RERANK_TOP_K",
    "rag.web.search.rerank_top_k",
    int(os.getenv("WEB_SEARCH_RERANK_TOP_K", "5")),
)
```

#### 2. Add to AppConfig class
**File**: `backend/open_webui/config.py`
**Location**: In the `AppConfig` class, after other web search configs

Find the `AppConfig` class and add:
```python
WEB_SEARCH_RERANK_TOP_K: int = WEB_SEARCH_RERANK_TOP_K.value
```

### Success Criteria:

#### Automated Verification:
- [ ] Backend linting passes: `npm run lint:backend`
- [ ] App starts without errors: `open-webui dev`

---

## Phase 2: Implement Reranking Logic

### Overview
Add the reranking step after search results are collected but before URLs are passed to the web loader.

### Changes Required:

#### 1. Add helper function for reranking search results
**File**: `backend/open_webui/routers/retrieval.py`
**Location**: Before the `process_web_search` function (around line 2100)

```python
async def rerank_search_results(
    search_results: List[SearchResult],
    query: str,
    reranking_function,
    top_k: int,
) -> List[SearchResult]:
    """Rerank search results by title+snippet relevance and return top-k.

    Args:
        search_results: List of SearchResult objects from SearXNG
        query: The search query to rank against
        reranking_function: The reranking function from app.state
        top_k: Number of results to return

    Returns:
        Top-k SearchResult objects sorted by relevance
    """
    if not reranking_function or not search_results:
        return search_results[:top_k] if top_k > 0 else search_results

    # Convert SearchResults to Documents for reranking
    # Use title + snippet as content since that's what we have
    docs = [
        Document(
            page_content=f"{item.title or ''}\n{item.snippet or ''}".strip(),
            metadata={"link": item.link, "original_index": i},
        )
        for i, item in enumerate(search_results)
        if item and item.link
    ]

    if not docs:
        return search_results[:top_k] if top_k > 0 else search_results

    try:
        # Get relevance scores from reranking function
        scores = await asyncio.to_thread(reranking_function, query, docs)

        # Pair results with scores and sort by score descending
        scored_results = list(zip(search_results, scores))
        scored_results.sort(key=lambda x: x[1], reverse=True)

        # Return top-k results
        top_results = [result for result, score in scored_results[:top_k]]

        log.debug(
            f"Reranked {len(search_results)} search results, "
            f"keeping top {len(top_results)} (scores: {[f'{s:.3f}' for _, s in scored_results[:top_k]]})"
        )

        return top_results
    except Exception as e:
        log.warning(f"Search result reranking failed, using original order: {e}")
        return search_results[:top_k] if top_k > 0 else search_results
```

#### 2. Add import for SearchResult
**File**: `backend/open_webui/routers/retrieval.py`
**Location**: In imports section (around line 1-50)

Verify this import exists, add if missing:
```python
from open_webui.retrieval.web.main import SearchResult
```

#### 3. Insert reranking call in process_web_search
**File**: `backend/open_webui/routers/retrieval.py`
**Location**: After line 2158 (after collecting result_items), before line 2160 (URL deduplication)

**Current code** (lines 2153-2161):
```python
        for result in search_results:
            if result:
                for item in result:
                    if item and item.link:
                        result_items.append(item)
                        urls.append(item.link)

        urls = list(dict.fromkeys(urls))
        log.debug(f"urls: {urls}")
```

**Change to**:
```python
        for result in search_results:
            if result:
                for item in result:
                    if item and item.link:
                        result_items.append(item)

        # Rerank search results before fetching (if reranking is enabled)
        rerank_top_k = request.app.state.config.WEB_SEARCH_RERANK_TOP_K
        if request.app.state.RERANKING_FUNCTION and rerank_top_k > 0:
            # Combine queries for reranking context
            combined_query = " ".join(form_data.queries)
            result_items = await rerank_search_results(
                search_results=result_items,
                query=combined_query,
                reranking_function=lambda q, docs: request.app.state.RERANKING_FUNCTION(
                    q, docs, user=user
                ),
                top_k=rerank_top_k,
            )
            log.debug(f"After reranking: {len(result_items)} results")

        # Extract URLs from (potentially reranked) results
        urls = [item.link for item in result_items if item and item.link]
        urls = list(dict.fromkeys(urls))  # Deduplicate while preserving order
        log.debug(f"urls: {urls}")
```

### Success Criteria:

#### Automated Verification:
- [ ] Type checking passes: `npm run check`
- [ ] Backend linting passes: `npm run lint:backend`
- [ ] App starts without errors: `open-webui dev`

#### Manual Verification:
- [ ] With reranking model configured:
  - [ ] Web search returns fewer URLs than SearXNG provided
  - [ ] Logs show reranking scores and selection
  - [ ] Search quality is equal or better
- [ ] Without reranking model:
  - [ ] Web search works as before (no reranking applied)
  - [ ] No errors in logs
- [ ] With `WEB_SEARCH_RERANK_TOP_K=0`:
  - [ ] Reranking is skipped (all results fetched)

**Implementation Note**: After completing this phase and all automated verification passes, test with a real web search query to verify reranking improves result relevance.

---

## Testing Strategy

### Manual Testing Steps:
1. Start the application with a reranking model configured
2. Enable web search in chat
3. Ask a specific question (e.g., "What is the capital of France?")
4. Check logs for:
   - Number of SearXNG results received
   - Reranking scores
   - Number of URLs actually fetched
5. Verify the answer quality

### Edge Cases to Test:
- Query with no relevant results (should still return top-k)
- Reranking function throws exception (should fallback gracefully)
- `WEB_SEARCH_RERANK_TOP_K` set to 0 (should skip reranking)
- `WEB_SEARCH_RERANK_TOP_K` greater than result count (should return all)
- Multiple queries in `form_data.queries` (should combine for reranking)

### Logging Verification:
With `LOG_LEVEL=DEBUG`, look for:
```
Reranked 10 search results, keeping top 5 (scores: [0.923, 0.891, 0.756, 0.712, 0.698])
After reranking: 5 results
urls: ['https://...', 'https://...', ...]
```

## Performance Considerations

**Expected improvements:**
- Fetch time: Reduced proportionally (e.g., 5 URLs instead of 10 = 50% faster)
- Embedding cost: Fewer documents to embed
- Vector DB: Less noise from irrelevant content
- Query quality: More relevant context in final response

**Overhead:**
- Reranking call: ~100-500ms depending on model
- Net gain: Significant when fetch time >> reranking time

## Configuration Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `WEB_SEARCH_RERANK_TOP_K` | `5` | Number of search results to keep after reranking. Set to `0` to disable pre-fetch reranking. |
| `RAG_RERANKING_MODEL` | (required) | Must be configured for reranking to work |

## References

- Research document: `thoughts/shared/research/2026-01-07-web-search-speed-optimization.md`
- Web search endpoint: `backend/open_webui/routers/retrieval.py:2105-2259`
- Reranking function: `backend/open_webui/retrieval/utils.py:908-918`
- SearchResult model: `backend/open_webui/retrieval/web/main.py:43-46`
- Config patterns: `backend/open_webui/config.py:3016-3020`
