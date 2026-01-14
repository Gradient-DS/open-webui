# Gradient Web Search Pipeline

> Sovereign AI web search architecture for Open WebUI with reranking, markdown extraction, and RAGAS benchmarking.

**Date**: 2026-01-08  
**Status**: Planning  
**Project**: NEO NL / Gradient-DS

---

## Table of Contents

- [Goals](#goals)
- [Architecture Overview](#architecture-overview)
- [Components](#components)
  - [Gradient Gateway](#gradient-gateway)
  - [SearXNG](#searxng)
  - [Extractor Service](#extractor-service)
  - [Reranker Service (Optional)](#reranker-service-optional)
- [Open WebUI Integration](#open-webui-integration)
- [Benchmarking with RAGAS](#benchmarking-with-ragas)
- [Docker Compose Setup](#docker-compose-setup)
- [Implementation Roadmap](#implementation-roadmap)

---

## Goals

1. **Sovereign hosting**: All components self-hosted in Dutch infrastructure, no external API calls (Jina, Firecrawl, etc.)
2. **Improved search quality**: Reranking of search results to reduce context poisoning from irrelevant sites
3. **Better content extraction**: Clean markdown extraction instead of raw HTML snippets
4. **Benchmarkable**: Same code path used in production can be evaluated with RAGAS
5. **Minimal Open WebUI changes**: Use native configuration, no forking required
6. **Modular**: Swap extractors, rerankers, or search engines without changing the rest

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           Open WebUI                                     │
│                                                                         │
│   SEARXNG_QUERY_URL=http://gradient-gateway:8000/search?q=<query>       │
│   RAG_WEB_LOADER_URL=http://gradient-gateway:8000/extract               │
│                                                                         │
│   ┌─────────────────────────────────────────────────────────────────┐   │
│   │  1. Query Generation Prompt → LLM → search queries              │   │
│   │  2. /search endpoint → URLs + snippets                          │   │
│   │  3. /extract endpoint → markdown content                        │   │
│   │  4. Embed + Store + Retrieve (native Open WebUI)                │   │
│   │  5. RAG Template Prompt → LLM → answer with citations           │   │
│   └─────────────────────────────────────────────────────────────────┘   │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        gradient-gateway                                  │
│                        (FastAPI thin proxy)                              │
│                                                                         │
│   GET  /search?q=...  ──→ SearXNG ──→ [rerank] ──→ results              │
│   POST /extract       ──→ Extractor ──→ markdown                        │
│   GET  /health        ──→ status of all services                        │
│                                                                         │
│   Logic layer:                                                          │
│   • Optional reranking between search and response                      │
│   • Extraction routing (static vs JS-heavy sites)                       │
│   • Caching (future)                                                    │
│   • Metrics/logging                                                     │
└───────────┬─────────────────────┬─────────────────────┬─────────────────┘
            │                     │                     │
            ▼                     ▼                     ▼
┌───────────────────┐  ┌───────────────────┐  ┌───────────────────┐
│     searxng       │  │    extractor      │  │    reranker       │
│                   │  │                   │  │    (optional)     │
│ DuckDuckGo, Brave │  │ Trafilatura /     │  │                   │
│ Qwant, Mojeek     │  │ Crawl4AI /        │  │ FlashRank /       │
│ + domain filters  │  │ Playwright        │  │ BGE-reranker      │
└───────────────────┘  └───────────────────┘  └───────────────────┘
```

---

## Components

### Gradient Gateway

Thin FastAPI service that proxies requests to downstream services with optional logic.

**Endpoints:**

| Endpoint | Method | Input | Output | Notes |
|----------|--------|-------|--------|-------|
| `/search` | GET | `?q=query&format=json` | SearXNG-compatible JSON | Proxies to SearXNG, optionally reranks |
| `/extract` | POST | `{"urls": [...]}` | `{"documents": [...]}` | Proxies to extractor service |
| `/health` | GET | - | Status of all services | For monitoring |

**Implementation:**

```python
# gateway/main.py
from fastapi import FastAPI, Query
from pydantic import BaseModel
import httpx
import os

app = FastAPI(title="Gradient Web Gateway")

SEARXNG_URL = os.getenv("SEARXNG_URL", "http://searxng:8080/search")
EXTRACTOR_URL = os.getenv("EXTRACTOR_URL", "http://extractor:8000/extract")
RERANKER_URL = os.getenv("RERANKER_URL", None)
RERANK_ENABLED = os.getenv("RERANK_ENABLED", "false").lower() == "true"


@app.get("/search")
async def search(
    q: str = Query(...),
    format: str = Query("json"),
    pageno: int = Query(1),
    language: str = Query("all"),
    safesearch: int = Query(0),
):
    """SearXNG-compatible search endpoint with optional reranking."""
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            SEARXNG_URL,
            params={
                "q": q,
                "format": format,
                "pageno": pageno,
                "language": language,
                "safesearch": safesearch,
            }
        )
        data = response.json()
    
    if RERANK_ENABLED and RERANKER_URL and data.get("results"):
        data["results"] = await _rerank_results(q, data["results"])
    
    return data


async def _rerank_results(query: str, results: list) -> list:
    """Call reranker service to reorder results by relevance."""
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            RERANKER_URL,
            json={
                "query": query,
                "passages": [
                    {"id": i, "text": f"{r.get('title', '')} {r.get('content', '')}"}
                    for i, r in enumerate(results)
                ]
            }
        )
        ranked = response.json()
    
    return [results[item["id"]] for item in ranked["results"]]


class ExtractRequest(BaseModel):
    urls: list[str]


@app.post("/extract")
async def extract(request: ExtractRequest):
    """External web loader endpoint for Open WebUI."""
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            EXTRACTOR_URL,
            json={"urls": request.urls}
        )
        return response.json()


@app.get("/health")
async def health():
    """Health check for all downstream services."""
    status = {"gateway": "ok"}
    async with httpx.AsyncClient(timeout=5) as client:
        try:
            await client.get(SEARXNG_URL.replace("/search", "/healthz"))
            status["searxng"] = "ok"
        except:
            status["searxng"] = "error"
        try:
            await client.get(EXTRACTOR_URL.replace("/extract", "/health"))
            status["extractor"] = "ok"
        except:
            status["extractor"] = "error"
    return status
```

---

### SearXNG

Self-hosted metasearch engine with domain filtering.

**Configuration** (`searxng/settings.yml`):

```yaml
use_default_settings: true

general:
  debug: false
  instance_name: "SearXNG - Gradient"

search:
  safe_search: 0
  autocomplete: ""
  default_lang: "auto"
  formats:
    - html
    - json

server:
  port: 8080
  bind_address: "0.0.0.0"
  secret_key: "${SEARXNG_SECRET_KEY}"
  limiter: false

outgoing:
  request_timeout: 5.0
  max_request_timeout: 10.0
  pool_connections: 100
  pool_maxsize: 20
  enable_http2: true

plugins:
  searx.plugins.hostnames.SXNGPlugin:
    active: true

hostnames:
  remove:
    # Chinese domains
    - '.*\.cn$'
    - '(.*\.)?baidu\.com$'
    - '(.*\.)?zhihu\.com$'
    - '(.*\.)?qq\.com$'
    
    # Social media (except LinkedIn)
    - '(.*\.)?facebook\.com$'
    - '(.*\.)?twitter\.com$'
    - '(.*\.)?x\.com$'
    - '(.*\.)?instagram\.com$'
    - '(.*\.)?tiktok\.com$'
    - '(.*\.)?reddit\.com$'
    - '(.*\.)?pinterest\.[a-z]+$'
    
    # Low quality
    - '(.*\.)?medium\.com$'
    - '(.*\.)?quora\.com$'
    - '.*\.xyz$'
    - '.*\.tk$'

  high_priority:
    - '(.*\.)?wikipedia\.org$'
    - '(.*\.)?github\.com$'
    - '.*\.edu$'
    - '.*\.gov$'
    - '.*\.gov\.nl$'
    - '(.*\.)?rijksoverheid\.nl$'
    - '(.*\.)?overheid\.nl$'

engines:
  - name: duckduckgo
    engine: duckduckgo
    weight: 1.4
    shortcut: ddg
    
  - name: brave
    engine: brave
    weight: 1.3
    shortcut: br
    
  - name: qwant
    engine: qwant
    weight: 1.2
    shortcut: qw
    
  - name: mojeek
    engine: mojeek
    weight: 1.0
    shortcut: mj
    
  - name: wikipedia
    engine: wikipedia
    weight: 1.3
    shortcut: wp
```

---

### Extractor Service

Converts URLs to clean markdown content.

**Options:**

| Tool | Best For | Resource Usage |
|------|----------|----------------|
| Trafilatura | Static pages, fastest | Low (~512MB) |
| Crawl4AI | JS-heavy sites | High (~4GB) |
| Playwright | Full browser rendering | High (~2GB) |
| Hybrid | Auto-route based on site | Medium |

**Simple Trafilatura Implementation:**

```python
# extractor/main.py
from fastapi import FastAPI
from pydantic import BaseModel
import httpx
import trafilatura
import asyncio

app = FastAPI(title="Gradient Extractor")


class ExtractRequest(BaseModel):
    urls: list[str]


@app.post("/extract")
async def extract(request: ExtractRequest):
    """Extract markdown from URLs."""
    tasks = [extract_url(url) for url in request.urls]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    documents = []
    for url, result in zip(request.urls, results):
        if isinstance(result, Exception):
            documents.append({"url": url, "content": "", "title": url, "error": str(result)})
        else:
            documents.append(result)
    
    return {"documents": documents}


async def extract_url(url: str) -> dict:
    """Extract content from single URL using trafilatura."""
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        response = await client.get(url)
        html = response.text
    
    content = trafilatura.extract(
        html,
        include_formatting=True,
        include_links=True,
        output_format="markdown"
    )
    
    title = trafilatura.extract(html, output_format="title") or url
    
    return {
        "url": url,
        "title": title,
        "content": content or "",
    }


@app.get("/health")
async def health():
    return {"status": "ok"}
```

**Hybrid Implementation (Future):**

```python
async def extract_url(url: str) -> dict:
    """Smart extraction: trafilatura first, crawl4ai fallback."""
    # Try trafilatura (fast, static)
    result = await extract_with_trafilatura(url)
    
    if result["content"] and len(result["content"]) > 200:
        return result
    
    # Fallback to crawl4ai for JS-heavy sites
    return await extract_with_crawl4ai(url)
```

---

### Reranker Service (Optional)

Reorders search results by semantic relevance to query.

**Options:**

| Model | Size | Speed | Quality |
|-------|------|-------|---------|
| ms-marco-TinyBERT-L2-v2 | 17M | ~12ms/100 | Good |
| ms-marco-MiniLM-L-12-v2 | 33M | ~50ms/100 | Better |
| bge-reranker-v2-m3 | 568M | ~300ms/100 | Best |

**Implementation:**

```python
# reranker/main.py
from fastapi import FastAPI
from pydantic import BaseModel
from flashrank import Ranker, RerankRequest
import os

app = FastAPI(title="Gradient Reranker")

MODEL = os.getenv("RERANKER_MODEL", "ms-marco-MiniLM-L-12-v2")
ranker = Ranker(model_name=MODEL)


class Passage(BaseModel):
    id: int
    text: str


class RerankRequestModel(BaseModel):
    query: str
    passages: list[Passage]
    top_k: int = 10


@app.post("/rerank")
async def rerank(request: RerankRequestModel):
    """Rerank passages by relevance to query."""
    passages = [{"id": p.id, "text": p.text} for p in request.passages]
    
    rerank_request = RerankRequest(query=request.query, passages=passages)
    results = ranker.rerank(rerank_request)
    
    return {
        "results": [
            {"id": r["id"], "score": r["score"]}
            for r in results[:request.top_k]
        ]
    }


@app.get("/health")
async def health():
    return {"status": "ok", "model": MODEL}
```

---

## Open WebUI Integration

**Environment Variables:**

```bash
# Web Search
ENABLE_RAG_WEB_SEARCH=true
RAG_WEB_SEARCH_ENGINE=searxng
SEARXNG_QUERY_URL=http://gradient-gateway:8000/search?q=<query>

# External Web Loader
RAG_WEB_LOADER_TYPE=external
RAG_WEB_LOADER_URL=http://gradient-gateway:8000/extract

# RAG Settings (unchanged)
ENABLE_RAG_HYBRID_SEARCH=true
RAG_TOP_K=10
```

**What Open WebUI Handles Natively:**
- Query generation (LLM call with prompt template)
- Embedding extracted content
- Vector storage (ChromaDB/etc)
- Retrieval (hybrid search, reranking)
- RAG template prompt injection
- Citation UI

---

## Benchmarking with RAGAS

### Key Insight

Open WebUI uses the **same two prompts** for both web search and document RAG:

1. **Query Generation Prompt** — converts user message to search queries
2. **RAG Template Prompt** — injects context and instructs LLM on citations

### Prompts to Replicate

```python
# genai_utils/evaluation/templates.py

QUERY_GENERATION_TEMPLATE = """### Task:
Analyze the chat history to determine the necessity of generating search queries.
By default, prioritize generating 1-3 broad and relevant search queries unless 
it is absolutely certain that no additional information is required.

### Chat History:
{messages}

### Guidelines:
- Generate concise, specific search queries
- Focus on the key information needed

### Response Format:
Return only the search queries, one per line. No explanations."""


RAG_TEMPLATE = """### Task:
Respond to the user query using the provided context, incorporating inline 
citations in the format [source_id] only when a <source_id> tag is explicitly 
provided in the context.

### Guidelines:
- If you don't know the answer, clearly state that.
- Respond in the same language as the user's query.
- Only include inline citations using [source_id] when provided.
- Do not use XML tags in your response.

<context>
{context}
</context>

<user_query>
{query}
</user_query>
"""


def format_web_source(idx: int, title: str, url: str, content: str) -> str:
    """Format a web search source (same as Open WebUI)."""
    return f'<source id="{idx}" name="{title}" url="{url}">\n{content}\n</source>'
```

### Benchmark Harness

```python
# genai_utils/evaluation/web_search_benchmark.py
import httpx
from datasets import Dataset
from ragas import evaluate
from ragas.metrics import faithfulness, context_precision, context_recall

from .templates import QUERY_GENERATION_TEMPLATE, RAG_TEMPLATE, format_web_source

GATEWAY_URL = "http://gradient-gateway:8000"


async def search_fn(queries: list[str]) -> list[dict]:
    """Call gradient-gateway /search (same as Open WebUI)."""
    all_results = []
    async with httpx.AsyncClient(timeout=30) as client:
        for q in queries:
            resp = await client.get(f"{GATEWAY_URL}/search", params={"q": q, "format": "json"})
            all_results.extend(resp.json().get("results", [])[:5])
    return all_results


async def extract_fn(urls: list[str]) -> list[dict]:
    """Call gradient-gateway /extract (same as Open WebUI)."""
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(f"{GATEWAY_URL}/extract", json={"urls": urls})
        return resp.json().get("documents", [])


async def benchmark_web_search(
    test_cases: list[dict],  # [{"query": str, "ground_truth": str}, ...]
    llm_fn,  # async (prompt: str) -> str
):
    """
    Benchmark web search pipeline with RAGAS.
    Uses same endpoints and prompts as Open WebUI.
    """
    results = []
    
    for case in test_cases:
        query = case["query"]
        
        # 1. Query generation (same prompt as Open WebUI)
        gen_prompt = QUERY_GENERATION_TEMPLATE.format(messages=f"User: {query}")
        queries_text = await llm_fn(gen_prompt)
        queries = [q.strip() for q in queries_text.strip().split("\n") if q.strip()][:3]
        
        # 2. Search (same endpoint as Open WebUI)
        search_results = await search_fn(queries)
        
        # 3. Extract (same endpoint as Open WebUI)
        urls = list(set(r["url"] for r in search_results))[:5]
        extracted = await extract_fn(urls)
        
        # 4. Format context (same format as Open WebUI)
        context = "\n\n".join([
            format_web_source(i+1, e["title"], e["url"], e["content"])
            for i, e in enumerate(extracted) if e.get("content")
        ])
        
        # 5. RAG prompt (same prompt as Open WebUI)
        rag_prompt = RAG_TEMPLATE.format(context=context, query=query)
        answer = await llm_fn(rag_prompt)
        
        results.append({
            "question": query,
            "contexts": [e["content"] for e in extracted if e.get("content")],
            "answer": answer,
            "ground_truth": case["ground_truth"],
        })
    
    # RAGAS evaluation
    dataset = Dataset.from_dict({
        "question": [r["question"] for r in results],
        "contexts": [r["contexts"] for r in results],
        "answer": [r["answer"] for r in results],
        "ground_truth": [r["ground_truth"] for r in results],
    })
    
    scores = evaluate(dataset, metrics=[faithfulness, context_precision, context_recall])
    
    return {"scores": scores, "results": results}
```

### Test Cases Example

```python
test_cases = [
    {
        "query": "What is the role of ANVS in nuclear safety?",
        "ground_truth": "ANVS (Autoriteit Nucleaire Veiligheid en Stralingsbescherming) is the Dutch nuclear safety authority responsible for supervising nuclear facilities and radiation protection."
    },
    {
        "query": "What are IAEA safety standards for research reactors?",
        "ground_truth": "IAEA safety standards for research reactors include..."
    },
]
```

---

## Docker Compose Setup

```yaml
# docker-compose.yml
version: "3.8"

services:
  # =============
  # Open WebUI
  # =============
  open-webui:
    image: ghcr.io/open-webui/open-webui:main
    environment:
      # Web search via gateway
      - ENABLE_RAG_WEB_SEARCH=true
      - RAG_WEB_SEARCH_ENGINE=searxng
      - SEARXNG_QUERY_URL=http://gradient-gateway:8000/search?q=<query>
      
      # External loader via gateway
      - RAG_WEB_LOADER_TYPE=external
      - RAG_WEB_LOADER_URL=http://gradient-gateway:8000/extract
      
      # RAG settings
      - ENABLE_RAG_HYBRID_SEARCH=true
      - RAG_TOP_K=10
    depends_on:
      - gradient-gateway
    ports:
      - "3000:8080"
    volumes:
      - open-webui-data:/app/backend/data

  # =============
  # Gateway
  # =============
  gradient-gateway:
    build: ./gateway
    environment:
      - SEARXNG_URL=http://searxng:8080/search
      - EXTRACTOR_URL=http://extractor:8000/extract
      - RERANK_ENABLED=true
      - RERANKER_URL=http://reranker:8000/rerank
    depends_on:
      - searxng
      - extractor
      - reranker
    ports:
      - "8000:8000"  # Expose for benchmarking

  # =============
  # Search
  # =============
  searxng:
    image: searxng/searxng:latest
    environment:
      - SEARXNG_SECRET_KEY=${SEARXNG_SECRET_KEY:-changeme}
    volumes:
      - ./searxng/settings.yml:/etc/searxng/settings.yml:ro

  # =============
  # Extractor
  # =============
  extractor:
    build: ./extractor
    # Or use existing image:
    # image: ghcr.io/crawl4ai/crawl4ai:latest

  # =============
  # Reranker (optional)
  # =============
  reranker:
    build: ./reranker
    environment:
      - RERANKER_MODEL=ms-marco-MiniLM-L-12-v2

volumes:
  open-webui-data:
```

---

## Implementation Roadmap

### Phase 1: Basic Pipeline (MVP)

- [ ] Set up SearXNG with domain filtering
- [ ] Create gradient-gateway with `/search` proxy (no reranking)
- [ ] Create extractor service with trafilatura
- [ ] Add `/extract` endpoint to gateway
- [ ] Configure Open WebUI environment variables
- [ ] Verify end-to-end flow works

### Phase 2: Quality Improvements

- [ ] Add reranker service (FlashRank)
- [ ] Enable reranking in gateway
- [ ] Set up RAGAS benchmark harness
- [ ] Create test cases for NEO NL domain
- [ ] Run baseline benchmarks
- [ ] Compare with/without reranking

### Phase 3: Advanced Features

- [ ] Hybrid extraction (trafilatura + crawl4ai fallback)
- [ ] Caching layer in gateway
- [ ] Prometheus metrics
- [ ] Ablation testing (query generation, reranker model, etc.)

### Phase 4: Deep Research (Optional)

- [ ] Separate "Deep Research" pipe for multi-step agentic search
- [ ] Query decomposition
- [ ] Iterative refinement
- [ ] Longer-running research tasks

---

## Summary

| Component | Responsibility | Container |
|-----------|---------------|-----------|
| **Open WebUI** | UI, query generation, embedding, retrieval, RAG prompt | `open-webui` |
| **Gateway** | Proxy + optional logic (reranking, routing) | `gradient-gateway` |
| **SearXNG** | Web search with domain filtering | `searxng` |
| **Extractor** | URL → markdown conversion | `extractor` |
| **Reranker** | Semantic relevance scoring | `reranker` |

**Key Benefits:**
- Open WebUI remains untouched (config only)
- Modular services can be swapped independently
- Same endpoints used for production and benchmarking
- Single source of truth for evaluation
