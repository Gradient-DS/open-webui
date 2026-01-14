---
date: 2026-01-08T10:04:55+01:00
researcher: Claude
git_commit: 44f06de1e260d11330f5ccff2ca58ffe71e26761
branch: feat/web
repository: Gradient-DS/open-webui
topic: "Extracting Web Search Pipeline for Standalone Benchmarking"
tags: [research, web-search, benchmarking, ragas, pipeline-extraction]
status: complete
last_updated: 2026-01-08
last_updated_by: Claude
---

# Research: Extracting Web Search Pipeline for Standalone Benchmarking

**Date**: 2026-01-08T10:04:55+01:00
**Researcher**: Claude
**Git Commit**: 44f06de1e260d11330f5ccff2ca58ffe71e26761
**Branch**: feat/web
**Repository**: Gradient-DS/open-webui

## Research Question

Is there an easy way to mimic the entire query → content flow that the LLM receives outside of Open WebUI? The goal is to run a web search pipeline against test queries and benchmark results with LLM-as-judge and RAGAS in genai-utils.

## Summary

**Yes, this is feasible with moderate effort.** The web search pipeline is relatively well-modularized, and there are two viable approaches:

1. **API-based approach** (Easiest): Use Open WebUI's existing REST endpoints with API key auth to run searches externally
2. **Extraction approach** (Most flexible): Extract the core search functions (~500 lines) into a standalone module

Key insight: The search engines themselves (`retrieval/web/*.py`) are mostly self-contained functions with minimal dependencies. The main coupling is to `request.app.state.config` for configuration values.

## Detailed Findings

### Web Search Pipeline Architecture

The full flow from query to LLM-ready content has 6 stages:

```
1. Query Generation (LLM) → 2. Search Execution → 3. Result Filtering
       ↓                           ↓                       ↓
4. Content Loading (optional) → 5. Vector Storage (optional) → 6. Context Assembly
```

#### Stage 1: Query Generation (`middleware.py:574-599`)
- Uses LLM to generate 1-3 search queries from chat context
- Template at `config.py:1900-1922`
- Can be bypassed by providing queries directly

#### Stage 2: Search Execution (`retrieval.py:1811-2102`)
- Dispatcher routes to configured search engine via `search_web()`
- 25+ providers supported, each in separate file:
  - `retrieval/web/searxng.py` - SearXNG (default for self-hosted)
  - `retrieval/web/brave.py` - Brave Search API
  - `retrieval/web/duckduckgo.py` - DuckDuckGo (no API key)
  - `retrieval/web/google_pse.py` - Google Programmable Search
  - `retrieval/web/tavily.py` - Tavily Search
  - `retrieval/web/perplexity.py` - Perplexity AI
  - `retrieval/web/jina_search.py` - Jina Search
  - `retrieval/web/serper.py` - Serper.dev
  - + 17 more

#### Stage 3: Result Filtering (`retrieval/web/main.py:12-40`)
- Filters against domain allowlist/blocklist
- Resolves hostnames for IP-based filtering

#### Stage 4: Content Loading (`retrieval/utils.py:433-769`)
- 5 loader types: `safe_web`, `playwright`, `firecrawl`, `tavily`, `external`
- `BYPASS_WEB_SEARCH_WEB_LOADER=true` skips this, uses snippets only

#### Stage 5: Vector Storage (`retrieval.py:2228-2243`)
- Saves to ChromaDB/Weaviate for RAG retrieval
- `BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL=true` skips this

#### Stage 6: Context Assembly (`middleware.py:1572-1611`)
- Formats sources as XML: `<source id="N" name="...">{content}</source>`
- Applies RAG template from `config.py:2878-2907`

### Coupling Analysis

**Loosely Coupled (Easy to Extract):**
- Search engine functions in `retrieval/web/*.py` - Just need API keys/URLs
- Result filtering in `retrieval/web/main.py` - Pure functions
- RAG template in `utils/task.py:187-225` - Simple string replacement

**Moderately Coupled:**
- Web loaders in `retrieval/utils.py` - Depend on some LangChain classes
- Query generation - Requires LLM call

**Tightly Coupled:**
- Configuration via `request.app.state.config` - Would need to mock/extract
- Event emission for WebSocket - Can be removed for standalone
- User authentication - Not needed for benchmarking

### Approach 1: API-Based Benchmarking (Recommended for Quick Start)

Use Open WebUI's REST API directly:

```python
# benchmark_web_search.py
import requests
from typing import List

class OpenWebUIClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url
        self.headers = {"Authorization": f"Bearer {api_key}"}

    def search_web(self, queries: List[str]) -> dict:
        """Call web search endpoint directly"""
        response = requests.post(
            f"{self.base_url}/api/v1/retrieval/process/web/search",
            headers=self.headers,
            json={"queries": queries}
        )
        return response.json()

    def generate_queries(self, messages: List[dict], prompt: str) -> List[str]:
        """Generate search queries from chat context"""
        response = requests.post(
            f"{self.base_url}/api/v1/tasks/queries/completions",
            headers=self.headers,
            json={
                "model": "your-model-id",
                "messages": messages,
                "prompt": prompt,
                "type": "web_search"
            }
        )
        return response.json().get("queries", [])

# Usage for benchmarking
client = OpenWebUIClient("http://localhost:8080", "sk-your-api-key")

test_queries = [
    "What is the capital of France?",
    "Latest news on climate change",
    # ... more test queries
]

results = []
for query in test_queries:
    search_result = client.search_web([query])
    results.append({
        "query": query,
        "sources": search_result.get("items", []),
        "docs": search_result.get("docs", [])
    })
```

**Pros:**
- Minimal code changes to Open WebUI
- Uses existing infrastructure
- Can benchmark current deployment

**Cons:**
- Requires running Open WebUI instance
- Limited control over intermediate steps
- Can't easily benchmark individual components

### Approach 2: Extracted Pipeline (Recommended for RAGAS)

Create a standalone module extracting core functions:

```python
# genai-utils/pipeline/web_search/search_engines.py
"""Extracted from Open WebUI retrieval/web/*.py"""

import httpx
from typing import List, Optional
from dataclasses import dataclass

@dataclass
class SearchResult:
    link: str
    title: Optional[str] = None
    snippet: Optional[str] = None

async def search_searxng(
    query: str,
    searxng_url: str,
    result_count: int = 10,
    **kwargs
) -> List[SearchResult]:
    """Adapted from open-webui/backend/open_webui/retrieval/web/searxng.py"""
    params = {
        "q": query,
        "format": "json",
        "pageno": 1,
        "safesearch": kwargs.get("safesearch", 1),
        "language": kwargs.get("language", "all"),
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(searxng_url, params=params)
        data = response.json()

    results = []
    for item in sorted(data.get("results", []), key=lambda x: x.get("score", 0), reverse=True):
        if len(results) >= result_count:
            break
        results.append(SearchResult(
            link=item.get("url"),
            title=item.get("title"),
            snippet=item.get("content")
        ))
    return results

async def search_brave(query: str, api_key: str, result_count: int = 10) -> List[SearchResult]:
    """Adapted from open-webui/backend/open_webui/retrieval/web/brave.py"""
    headers = {"X-Subscription-Token": api_key, "Accept": "application/json"}
    url = f"https://api.search.brave.com/res/v1/web/search?q={query}&count={result_count}"

    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers)
        data = response.json()

    return [
        SearchResult(link=r["url"], title=r.get("title"), snippet=r.get("description"))
        for r in data.get("web", {}).get("results", [])[:result_count]
    ]

# ... Similar extractions for duckduckgo, google_pse, tavily, etc.
```

```python
# genai-utils/pipeline/web_search/context_builder.py
"""Adapted from Open WebUI middleware.py and task.py"""

from typing import List, Dict, Any

DEFAULT_RAG_TEMPLATE = """### Task:
Respond to the user query using the provided context, incorporating inline citations...

<context>
{{CONTEXT}}
</context>
"""

def build_context_string(sources: List[Dict[str, Any]]) -> str:
    """Build XML-tagged context from sources (middleware.py:1572-1598)"""
    context_string = ""
    citation_idx = 1

    for source in sources:
        source_id = source.get("id", citation_idx)
        source_name = source.get("name", "")
        content = source.get("content", source.get("snippet", ""))

        context_string += f'<source id="{citation_idx}"'
        if source_name:
            context_string += f' name="{source_name}"'
        context_string += f">{content}</source>\n"
        citation_idx += 1

    return context_string

def apply_rag_template(template: str, context: str, query: str) -> str:
    """Apply RAG template (task.py:187-225)"""
    if not template.strip():
        template = DEFAULT_RAG_TEMPLATE

    template = template.replace("{{CONTEXT}}", context)
    template = template.replace("{{QUERY}}", query)
    return template
```

```python
# genai-utils/pipeline/web_search/benchmark.py
"""Web search benchmarking with RAGAS integration"""

import asyncio
from ragas import evaluate
from ragas.metrics import (
    faithfulness,
    answer_relevancy,
    context_precision,
    context_recall,
)
from datasets import Dataset

from .search_engines import search_searxng, search_brave
from .context_builder import build_context_string, apply_rag_template

async def run_web_search_benchmark(
    test_queries: List[Dict],  # {"query": str, "ground_truth": str}
    search_fn,  # Search function to benchmark
    llm_fn,  # LLM for generating answers
    **search_kwargs
):
    """Run benchmark and evaluate with RAGAS"""

    results = {
        "question": [],
        "answer": [],
        "contexts": [],
        "ground_truth": [],
    }

    for test_case in test_queries:
        query = test_case["query"]

        # Run search
        search_results = await search_fn(query, **search_kwargs)

        # Build context
        context_string = build_context_string([
            {"content": r.snippet, "name": r.title} for r in search_results
        ])

        # Get LLM answer
        prompt = apply_rag_template("", context_string, query)
        answer = await llm_fn(prompt)

        results["question"].append(query)
        results["answer"].append(answer)
        results["contexts"].append([r.snippet for r in search_results])
        results["ground_truth"].append(test_case["ground_truth"])

    # Evaluate with RAGAS
    dataset = Dataset.from_dict(results)
    score = evaluate(
        dataset,
        metrics=[faithfulness, answer_relevancy, context_precision, context_recall]
    )

    return score
```

**Files to Extract (~500 lines core):**

| Source File | Lines | What to Extract |
|-------------|-------|-----------------|
| `retrieval/web/searxng.py` | 10-89 | `search_searxng()` |
| `retrieval/web/brave.py` | 10-42 | `search_brave()` |
| `retrieval/web/duckduckgo.py` | 11-50 | `search_duckduckgo()` |
| `retrieval/web/google_pse.py` | 10-72 | `search_google_pse()` |
| `retrieval/web/tavily.py` | 10-60 | `search_tavily()` |
| `retrieval/web/main.py` | 12-40 | `get_filtered_results()` |
| `middleware.py` | 1572-1598 | Context assembly logic |
| `utils/task.py` | 187-225 | `rag_template()` |
| `config.py` | 2878-2907 | `DEFAULT_RAG_TEMPLATE` |

### Configuration Bypass Options

Open WebUI has built-in bypass flags useful for benchmarking:

```python
# In Open WebUI config (env vars):
BYPASS_WEB_SEARCH_WEB_LOADER=true     # Use snippets only, skip page loading
BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL=true  # Return raw docs, skip vector DB
```

With both flags enabled, the web search endpoint returns raw search results without vector storage - ideal for benchmarking.

### Integration with genai-utils

The extracted pipeline would fit into genai-utils structure:

```
genai-utils/
├── pipeline/
│   └── web_search/           # New module
│       ├── __init__.py
│       ├── search_engines.py  # Extracted search functions
│       ├── context_builder.py # RAG template + context assembly
│       ├── loaders.py         # Optional: page content loading
│       └── benchmark.py       # RAGAS integration
├── evaluation/
│   └── web_search_eval.py    # LLM-as-judge metrics
```

## Code References

- `backend/open_webui/retrieval/web/*.py` - Search engine implementations
- `backend/open_webui/routers/retrieval.py:2105-2259` - `process_web_search()` endpoint
- `backend/open_webui/utils/middleware.py:555-714` - `chat_web_search_handler()`
- `backend/open_webui/utils/middleware.py:1572-1611` - Context assembly
- `backend/open_webui/utils/task.py:187-225` - `rag_template()` function
- `backend/open_webui/config.py:2878-2907` - Default RAG template
- `backend/open_webui/config.py:1900-1922` - Query generation template

## Architecture Insights

1. **Search engines are pluggable** - Each engine is a standalone async function returning `List[SearchResult]`
2. **Two-phase retrieval** - Query generation (LLM) + actual search (API calls)
3. **Bypass flags exist** - Can skip content loading and vector storage for raw results
4. **XML-based citations** - Context uses `<source id="N">` tags for citation tracking

## Recommendations

### For Quick Benchmarking (Today)
1. Enable bypass flags in Open WebUI
2. Use API endpoints to run searches
3. Collect raw results + LLM responses
4. Evaluate with simple metrics (latency, relevance)

### For RAGAS Integration (This Week)
1. Extract core search functions to genai-utils
2. Create RAGAS-compatible benchmark harness
3. Use LLM-as-judge for answer quality
4. Compare different search engines side-by-side

### For Full Pipeline Isolation (Future)
1. Extract complete pipeline as standalone package
2. Add configurable components (search, loader, embedder)
3. Create test fixtures for reproducible benchmarks
4. Integrate with CI for regression testing

## Open Questions

1. **Which search engines to prioritize?** SearXNG (self-hosted) vs Brave (paid API) vs DuckDuckGo (free)?
2. **Include content loading?** Full page content improves quality but adds latency
3. **Vector storage needed?** Or test snippet-only retrieval first?
4. **Ground truth dataset?** Need to create test queries with expected answers

## Related Files

- `backend/open_webui/retrieval/utils.py:921-1195` - `get_sources_from_items()` for full retrieval flow
- `backend/open_webui/routers/tasks.py:460-541` - Query generation endpoint
- `backend/open_webui/retrieval/utils.py:208-315` - Hybrid search implementation
