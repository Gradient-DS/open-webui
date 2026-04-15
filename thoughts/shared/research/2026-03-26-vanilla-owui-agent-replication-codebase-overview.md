---
date: 2026-03-26T19:30:00+02:00
researcher: Claude Code
git_commit: 84d62d6bd6abd3d83626a54906d97bb7381a619d
branch: feat/external-base-agents
repository: Gradient-DS/open-webui
topic: 'Vanilla OWUI Agent Replication — High-Level Codebase Overview'
tags: [research, codebase, agent-api, pipeline, rag, web-search, weaviate, tools, replication]
status: complete
last_updated: 2026-03-26
last_updated_by: Claude Code
---

# Vanilla OWUI Agent Replication — High-Level Codebase Overview

**Purpose**: Map all code relevant to building an external agent that replicates vanilla Open WebUI's pipeline behavior: RAG retrieval, web search, tool calling, source injection, and SSE streaming.

**Companion doc**: `2026-03-26-vanilla-owui-pipeline-spec.md` — documents prompts and pipeline steps from the Open WebUI side.

---

## 1. Architecture Summary

```
Open WebUI (middleware.py)
│
├─ Pre-processing (always runs):
│   system prompt, memory, voice, image gen, skills, inlet filters
│
├─ AGENT_API_ENABLED=true → bypass:
│   web search, RAG, tool resolution, source injection
│
├─ call_agent_api() → POST to external agent
│   ├─ AgentPayload: messages, features, files, knowledge, tool_ids
│   └─ SSE response: status/source/data events
│
└─ Post-processing (always runs):
    process_chat_response, title/tag/followup gen, DB persistence

External Agent (genai-utils/agents_updated/)
│
├─ FastAPI service at /v1/chat/completions
├─ Agent framework (LangGraph-based)
├─ Retrieval providers (HTTP→search-api→Weaviate, or OWUI callback)
├─ SSE streaming with status/source/data events
└─ Project-specific agent flows (mkbot, neo_nl)
```

---

## 2. Open WebUI Side — Key Files

### 2.1 Agent API Client

| File                                   | Key Contents                                                                               |
| -------------------------------------- | ------------------------------------------------------------------------------------------ |
| `backend/open_webui/env.py:873-875`    | `AGENT_API_ENABLED`, `AGENT_API_BASE_URL`, `AGENT_API_AGENT` env vars                      |
| `backend/open_webui/utils/agent.py`    | Full agent transport layer                                                                 |
| — `:54-78`                             | `AgentPayload` dataclass (all fields)                                                      |
| — `:81-119`                            | `build_agent_payload()` — constructs payload from form_data/metadata                       |
| — `:130-194`                           | `stream_agent_response()` — aiohttp SSE client, parses event:/data: lines                  |
| — `:202-256`                           | `call_agent_api()` — entry point called from main.py                                       |
| — `:284-359`                           | `_build_streaming_response()` — dispatches status/source via Socket.IO, yields data events |
| `backend/open_webui/main.py:2100-2103` | Branch: if AGENT_API_ENABLED → call_agent_api() instead of chat_completion_handler()       |

### 2.2 Middleware Bypass Points

| File (middleware.py) | What's Bypassed                                                                       |
| -------------------- | ------------------------------------------------------------------------------------- |
| `:2271-2274`         | Knowledge flattening (model KB → file items) — skipped, raw knowledge passed to agent |
| `:2370-2371`         | `chat_web_search_handler()` — skipped                                                 |
| `:2394-2396`         | Code interpreter prompt injection — skipped                                           |
| `:2506-2508`         | Early return: skips tool resolution, file context, source injection                   |

**Still runs** before bypass: system prompt, URL→base64, inlet filters, voice prompt, memory injection, image generation, skills injection.

### 2.3 Web Search Pipeline

| File                             | Key Contents                                                                      |
| -------------------------------- | --------------------------------------------------------------------------------- |
| `utils/middleware.py:1447-1608`  | `chat_web_search_handler()` — full orchestration                                  |
| — `:1467-1499`                   | Query generation via `generate_queries()`                                         |
| — `:1530-1566`                   | Results → file items (collection_name or raw docs)                                |
| — `:1451-1606`                   | SSE status events (web_search, web_search_queries_generated)                      |
| `routers/retrieval.py:2248-2559` | `search_web()` — dispatcher to 28 engine adapters                                 |
| `routers/retrieval.py:2562-2731` | `process_web_search()` — content loading, embedding, collection creation          |
| `retrieval/web/main.py:43-46`    | `SearchResult` model (link, title, snippet)                                       |
| `retrieval/web/utils.py:656-733` | `get_web_loader()` — factory for SafeWebBaseLoader, Playwright, Firecrawl, Tavily |
| `config.py:3641-3663`            | Web search config: `WEB_SEARCH_ENGINE`, `BYPASS_WEB_SEARCH_*` flags               |

**Supported engines** (28): Google PSE, Brave, SearXNG, DuckDuckGo, Bing, Tavily, Exa, Jina, Serper, Serpapi, Firecrawl, Perplexity, Yandex, and more.

### 2.4 RAG / Vector DB Pipeline

| File                               | Key Contents                                                                   |
| ---------------------------------- | ------------------------------------------------------------------------------ |
| `retrieval/vector/main.py`         | `VectorDBBase` abstract class, `VectorItem`, `SearchResult`, `GetResult`       |
| `retrieval/vector/factory.py`      | `Vector.get_vector()` — factory, `VECTOR_DB_CLIENT` singleton                  |
| `retrieval/vector/dbs/weaviate.py` | Weaviate adapter (connect, search, insert, upsert, delete)                     |
| — `:74-98`                         | Connection via `weaviate.connect_to_custom()` (HTTP + gRPC)                    |
| — `:100-122`                       | `_sanitize_collection_name()` — hyphen→underscore, capitalize                  |
| — `:135-176`                       | `_create_collection()` — 9 explicit TEXT properties                            |
| — `:235-301`                       | `search()` — `near_vector()`, distance→similarity conversion `(2-dist)/2`      |
| `retrieval/utils.py:983-1272`      | `get_sources_from_items()` — resolves files/collections/notes/chats to sources |
| — `:1008-1042`                     | text items                                                                     |
| — `:1044-1086`                     | note/chat items                                                                |
| — `:1095-1139`                     | file items (full content or collection search)                                 |
| — `:1141-1192`                     | collection items (KB resolution)                                               |
| — `:1209-1249`                     | Vector search dispatch (full context / hybrid / standard)                      |
| `retrieval/utils.py:220-339`       | `query_doc_with_hybrid_search()` — BM25 + vector ensemble + reranking          |
| `retrieval/utils.py:1325-1403`     | `RerankCompressor` — ColBERT or external reranker                              |
| `routers/retrieval.py:1527-1738`   | `save_docs_to_vector_db()` — split, embed, insert                              |
| `routers/tasks.py:475-555`         | `generate_queries()` — LLM-based query generation                              |

**Config** (config.py):

- `RAG_TOP_K`: 3 (default)
- `RAG_TOP_K_RERANKER`: 3
- `RELEVANCE_THRESHOLD`: 0.0
- `ENABLE_RAG_HYBRID_SEARCH`: false
- `RAG_HYBRID_BM25_WEIGHT`: 0.5
- `RAG_FULL_CONTEXT`: false
- `VECTOR_DB`: "chroma" (default, our deployment uses "weaviate")

### 2.5 Source Context Injection

| File                          | Key Contents                                                                                 |
| ----------------------------- | -------------------------------------------------------------------------------------------- |
| `utils/middleware.py:895-918` | `get_source_context()` — formats sources as `<source id="N" name="...">` XML                 |
| `utils/middleware.py:921-956` | `apply_source_context_to_messages()` — injects via RAG template                              |
| — `:945-950`                  | If `RAG_SYSTEM_CONTEXT=true` → append to system message                                      |
| — `:951-956`                  | If `false` (default) → prepend to user message                                               |
| `utils/task.py:270-308`       | `rag_template()` — substitutes `{{CONTEXT}}` and `{{QUERY}}`                                 |
| `utils/misc.py:332-393`       | `update_message_content()`, `add_or_update_system_message()`, `add_or_update_user_message()` |
| `config.py:3528-3558`         | `DEFAULT_RAG_TEMPLATE` and `RAG_TEMPLATE` PersistentConfig                                   |
| `env.py:435`                  | `RAG_SYSTEM_CONTEXT` — boolean, default false                                                |

### 2.6 Tool Calling System

| File                            | Key Contents                                                                           |
| ------------------------------- | -------------------------------------------------------------------------------------- |
| `utils/tools.py:147-400`        | `get_tools()` — resolves tool_ids to callable+spec dicts                               |
| — `:159-248`                    | Local (DB) tool resolution via `load_tool_module_by_id()`                              |
| — `:250-398`                    | External OpenAPI tool server resolution                                                |
| `utils/tools.py:403-540`        | `get_builtin_tools()` — search_web, fetch_url, query_knowledge_files, view_skill, etc. |
| `utils/mcp/client.py:35-108`    | `MCPClient` — connect, list_tool_specs, call_tool                                      |
| `utils/middleware.py:2527-2670` | MCP tool discovery inline in middleware                                                |
| `utils/middleware.py:1231-1405` | Non-native tool calling (prompt-based, single-shot)                                    |
| `utils/middleware.py:4258-4595` | Native tool calling (iterative loop with re-prompting)                                 |
| `utils/middleware.py:216-388`   | `get_citation_source_from_tool_result()` — tool results → citation sources             |
| `tools/builtin.py`              | All builtin tool implementations                                                       |
| `config.py:2373-2394`           | `DEFAULT_TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE`                                       |

### 2.7 Skills System

| File                            | Key Contents                                           |
| ------------------------------- | ------------------------------------------------------ |
| `utils/middleware.py:2429-2467` | Skill resolution and injection                         |
| — `:2451-2457`                  | User-selected skills: full content in `<skill>` tags   |
| — `:2459-2466`                  | Model-attached skills: summary in `<available_skills>` |
| `tools/builtin.py:2047-2101`    | `view_skill` builtin — lazy-loads full skill content   |
| `models/skills.py`              | Skill DB model (name, description, content, is_active) |

---

## 3. External Agent Side — Existing Implementation

### 3.1 agents_updated (Current Architecture)

Located at `genai-utils/agents_updated/` — standalone FastAPI service.

| Directory                          | Purpose                                                                      |
| ---------------------------------- | ---------------------------------------------------------------------------- |
| `service/`                         | FastAPI app, routes (`/chat/completions`), SSE streaming                     |
| `agents/`                          | Agent framework: base, runner, registry, state, LangGraph integration        |
| `agents/flows/`                    | Concrete agent implementations (mkbot_v1, neo_nl_v2, simple_rag)             |
| `agents/retrieval/`                | Agent-level search orchestration, filters, serialization                     |
| `agents/streaming/`                | Answer streaming                                                             |
| `agents/context/`                  | Token budget, context rendering                                              |
| `agents/citations.py`              | Citation handling                                                            |
| `retrieval/`                       | Retrieval abstraction layer                                                  |
| `retrieval/interface.py`           | Abstract `RetrievalProvider`, `SearchMode`, `ProviderCapabilities`           |
| `retrieval/providers/http.py`      | `HttpRetrievalProvider` — calls search-api (Flask sidecar fronting Weaviate) |
| `retrieval/providers/openwebui.py` | `OpenWebUIRetrievalProvider` — calls back to OWUI's knowledge API            |
| `retrieval/reranking.py`           | Reranking logic                                                              |
| `llm/caller.py`                    | LLM calling                                                                  |
| `core/`                            | Types, config, protocols                                                     |
| `state/`                           | Checkpointer, memory (Postgres-backed)                                       |

**SSE streaming** (`service/streaming.py`):

- `stream_events_to_sse()` — converts agent events to SSE format
- `_format_status_update()` — status events for UI progress
- `_format_source_citation()` — source events for citations
- `_format_content_chunk()` — OpenAI-compatible content chunks

### 3.2 agents (Older Architecture)

Located at `genai-utils/agents/` — OpenWebUI Pipe-based approach (loaded into OWUI process).

| File                          | Purpose                                             |
| ----------------------------- | --------------------------------------------------- |
| `src/agents/openwebui.py`     | `OpenWebUIAdapter`, message conversion              |
| `src/agents/pipes/factory.py` | `create_agent_pipe()` — generates OWUI Pipe classes |
| `src/agents/retrieval.py`     | Retrieval logic                                     |
| `src/agents/citations.py`     | Citation handling                                   |

### 3.3 Deployment

| File                                                    | Purpose                                        |
| ------------------------------------------------------- | ---------------------------------------------- |
| `genai-utils/helm/agent-stack/`                         | Helm chart: agents-api + search-api + Weaviate |
| `open-webui/helm/open-webui-tenant/values.yaml:355-357` | `agentApiEnabled/BaseUrl/Agent` Helm values    |
| `soev-gitops/tenants/previder-prod/mkbot/`              | Production deployment values                   |

---

## 4. What the External Agent Must Implement (for Vanilla Replication)

Based on the bypass analysis, the agent needs to replicate these OWUI pipeline steps:

### 4.1 Web Search (if `features.web_search = true`)

**OWUI does:**

1. Generate 1-3 search queries via LLM (`QUERY_GENERATION_PROMPT_TEMPLATE`)
2. Execute queries against configured engine (28 adapters)
3. Load full page content (or use snippets if `BYPASS_WEB_SEARCH_WEB_LOADER`)
4. Embed into vector DB collection `web-search-{hash}` (or bypass)
5. Retrieve via similarity search
6. Emit status events: `web_search`, `web_search_queries_generated`

**Agent must:** Replicate steps 1-5, emit matching SSE events.

**Open question:** Which search engine will the agent use? OWUI's `WEB_SEARCH_ENGINE` config won't be passed to the agent. Options:

- Call back to OWUI's `/api/v1/retrieval/search` endpoint
- Bundle own search adapter (SearXNG recommended — self-hosted, no API key)
- Accept search engine config as agent parameter

### 4.2 RAG Retrieval (if `knowledge` or `files` present)

**OWUI does:**

1. Resolve knowledge/file items to collection names
2. Generate retrieval queries via LLM
3. Query vector DB collections (via `VECTOR_DB_CLIENT`)
4. Optional: hybrid BM25+vector search, reranking
5. Format as `<source>` XML tags
6. Inject into messages via RAG template

**Agent must:** Query the same Weaviate instance.

**Existing interface:** `agents_updated/retrieval/providers/` has:

- `http.py` — `HttpRetrievalProvider` talks to search-api sidecar (Flask → Weaviate)
- `openwebui.py` — `OpenWebUIRetrievalProvider` calls back to OWUI API

**Key consideration:** The agent receives `knowledge[].collection_names` — these map directly to Weaviate collection names (after sanitization: hyphens→underscores, capitalize first char).

### 4.3 Source Context Injection

**OWUI does:**

1. Format sources as `<source id="N" name="...">content</source>`
2. Apply RAG template with `{{CONTEXT}}` substitution
3. Inject into system message (if `RAG_SYSTEM_CONTEXT=true`) or user message (default)

**Agent must:** Replicate the same formatting. The default RAG template is documented in the companion spec.

### 4.4 Tool Calling (if `tool_ids` present)

**OWUI does:**

1. Resolve tool_ids → specs + callables
2. Either native FC (pass to LLM) or prompt-based (secondary LLM call)
3. Execute tools, inject results as sources

**Agent must:** This is the most complex area. Options:

- Call back to OWUI's tool execution API
- Resolve and execute tools independently (requires access to tool specs/MCP servers)
- For builtin tools (search_web, fetch_url, query_knowledge_files): implement natively

### 4.5 Code Interpreter Output

**Agent must:** Output `<code_interpreter type="code" lang="python">...</code_interpreter>` tags. OWUI handles execution via `process_chat_response`.

### 4.6 SSE Event Format

The agent must emit these SSE events for proper UI integration:

```
event: status
data: {"description": "...", "action": "web_search|knowledge_search|...", "done": bool}

event: source
data: {"name": "...", "url": "...", "id": "..."}

data: {"choices": [{"delta": {"content": "..."}}]}  # OpenAI streaming format

data: [DONE]
```

---

## 5. Suggested Research Breakdown (Next Steps)

### Plan 1: Vanilla RAG Agent Flow

Design the agent flow that replicates OWUI's non-native RAG pipeline:

- Query generation → Weaviate search → source formatting → context injection → LLM call
- Use existing `HttpRetrievalProvider` or build direct Weaviate client

### Plan 2: Web Search Integration

Decide on search engine strategy and implement:

- Search query generation
- Engine adapter (SearXNG callback, or new)
- Content loading and optional embedding
- SSE status events

### Plan 3: Tool Calling Strategy

Design tool execution for the external agent:

- Which tools to support natively vs. callback to OWUI
- MCP server connection from agent
- Native FC vs. prompt-based approach

### Plan 4: SSE Streaming Contract

Formalize the SSE event contract between agent and OWUI:

- Map all status/source/data events
- Test with OWUI's `_build_streaming_response()` parsing

---

## Code References

- `backend/open_webui/utils/agent.py` — Agent API transport layer
- `backend/open_webui/utils/middleware.py` — Main pipeline orchestration (5000+ lines)
- `backend/open_webui/retrieval/utils.py` — RAG search, embedding, reranking
- `backend/open_webui/retrieval/vector/dbs/weaviate.py` — Weaviate adapter
- `backend/open_webui/routers/retrieval.py` — Web search dispatcher, document processing
- `backend/open_webui/routers/tasks.py` — Query/title/tag generation
- `backend/open_webui/utils/tools.py` — Tool resolution
- `backend/open_webui/tools/builtin.py` — Builtin tool implementations
- `backend/open_webui/config.py` — All configuration defaults
- `genai-utils/agents_updated/` — Existing external agent framework
- `genai-utils/agents_updated/retrieval/` — Existing retrieval providers (HTTP, OWUI callback)
- `genai-utils/agents_updated/service/streaming.py` — SSE formatting
