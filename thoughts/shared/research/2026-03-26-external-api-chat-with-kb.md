---
date: 2026-03-26T14:00:00+02:00
researcher: Claude
git_commit: c0db5407764a3c167d1c971ee01b26284e7c2e28
branch: dev
repository: open-webui
topic: "External API: Chat with Knowledge Bases — Streaming Responses for Third-Party UI Consumers"
tags: [research, codebase, external-api, chat, streaming, sse, knowledge-base, rag, integrations, agents]
status: complete
last_updated: 2026-03-26
last_updated_by: Claude
---

# Research: External API for Chat with Knowledge Bases

**Date**: 2026-03-26T14:00:00+02:00
**Researcher**: Claude
**Git Commit**: c0db5407764a3c167d1c971ee01b26284e7c2e28
**Branch**: dev
**Repository**: open-webui

## Research Question

How can we expose our API to external parties so they can: (1) upload documents to a knowledge base, (2) start a chat with a KB or document and send a query, (3) receive streaming status messages and answers back — all without needing our UI? What is the feasibility and estimated effort?

## Summary

**Good news: ~70% of the infrastructure already exists.** The push integration handles uploads, API key auth works, and the chat completions endpoint already has an SSE passthrough path for non-WebSocket consumers. The main work is (a) making the SSE path emit the same rich status events (RAG progress, source citations) that currently only go through WebSocket, (b) creating a simplified "stateless query" endpoint that doesn't require chat session management, and (c) documenting the external API contract.

**Estimated total effort: Medium (2-3 weeks)**, broken into phases:

| Phase | Effort | Description |
|-------|--------|-------------|
| Phase 1: Stateless query endpoint | ~1 week | New `/api/v1/integrations/query` endpoint that accepts a KB + question, returns SSE stream |
| Phase 2: Rich SSE events | ~3-4 days | Pipe status/citation/source events into the SSE stream (not just token deltas) |
| Phase 3: Documentation & SDK helpers | ~2-3 days | OpenAPI docs, example clients (Python, JS), auth guide |
| Phase 4: Agent support | ~TBD | When custom soev.ai agents land, expose agent selection in the query endpoint |

## Detailed Findings

### What Already Exists

#### 1. Document Upload (Complete ✅)

The push integration at `POST /api/v1/integrations/ingest` fully handles:
- Creating/finding knowledge bases by `source_id`
- Uploading documents in three formats: `parsed_text`, `chunked_text`, `full_documents`
- Embedding and storing in the vector DB
- File record management with idempotent upserts
- Service account auth via API keys (`sk-` prefix)

**External parties already use this.** No changes needed here.

#### 2. API Key Authentication (Complete ✅)

- API keys (`sk-` prefix) authenticate via `Authorization: Bearer sk-xxxxx`
- Keys resolve to a user identity with full permissions
- Integration service accounts have `user.info.integration_provider` binding
- `ENABLE_API_KEYS` must be true, user needs `features.api_keys` permission
- Endpoint restrictions can be applied via `API_KEYS_ALLOWED_ENDPOINTS`

#### 3. Chat Completions with SSE Passthrough (Partially exists ⚠️)

`POST /api/chat/completions` at `main.py:1922` has **two dispatch paths**:

**Path A — WebSocket mode** (current frontend): When `session_id`, `chat_id`, and `message_id` are all present, returns `{"status": true, "task_id": "..."}` immediately. Streaming goes through Socket.IO.

**Path B — SSE passthrough** (API consumers): When any of those three are missing, `process_chat()` runs synchronously and returns the response directly. For streaming, this returns a `StreamingResponse` with `text/event-stream` content type at `middleware.py:4851`.

**Path B is what external parties need**, but it currently has limitations:
- Status events (RAG search progress, query generation) are only emitted via `event_emitter` (WebSocket)
- Source/citation events only go through WebSocket
- The SSE stream only contains the raw LLM token deltas, not the rich events
- No error recovery or structured error events in the SSE stream

#### 4. RAG Context Injection (Complete ✅)

The middleware pipeline at `middleware.py:2146` automatically:
- Collects knowledge base files from `form_data.files` or model config
- Generates retrieval queries from the conversation
- Runs vector search (pure or hybrid with BM25 + reranking)
- Injects `<source>` XML context into the LLM prompt
- All of this happens in `process_chat_payload()` before the LLM call

**This works identically for both WebSocket and SSE paths.** The RAG context is injected at the message level, not the transport level.

### What Needs to Be Built

#### Component 1: Stateless Query Endpoint (~1 week)

The current `/api/chat/completions` requires building an OpenAI-compatible payload with messages, model selection, file references, etc. External parties shouldn't need to understand our internal message format.

**Proposed endpoint:** `POST /api/v1/integrations/query`

```python
class QueryRequest(BaseModel):
    collection_source_id: str           # External KB identifier
    query: str                          # The user's question
    model: Optional[str] = None         # Override default model
    stream: bool = True                 # SSE streaming (default)
    conversation_id: Optional[str] = None  # For multi-turn conversations
    system_prompt: Optional[str] = None    # Custom system prompt
    params: Optional[dict] = None          # Temperature, top_p, etc.

class QueryResponse(BaseModel):
    answer: str                         # Full response text
    sources: list[Source]               # Retrieved sources with citations
    usage: Optional[dict] = None        # Token usage
    conversation_id: str                # For follow-up queries
```

**Implementation approach:**
1. Resolve `collection_source_id` → knowledge base ID using existing `_find_kb_by_source_id()`
2. Build the internal `form_data` with:
   - `messages`: `[{"role": "user", "content": query}]` (or load conversation history if `conversation_id` provided)
   - `files`: `[{"type": "collection", "id": knowledge_id}]`
   - `model`: from request or provider config default
   - `stream`: from request
3. Call the existing `process_chat()` pipeline (which handles RAG injection + LLM call)
4. Return SSE stream or JSON response

**Key advantage:** This is a thin wrapper around existing infrastructure. The RAG pipeline, model routing, and streaming all reuse current code.

**Multi-turn conversations:** Store messages keyed by `conversation_id`. The external party sends the same `conversation_id` for follow-up questions. We maintain the conversation history server-side so the LLM has context. This maps naturally to the existing `Chat` model.

#### Component 2: Rich SSE Events (~3-4 days)

The SSE stream should include structured events beyond just token deltas:

```
event: status
data: {"type": "retrieval", "message": "Searching knowledge base..."}

event: status
data: {"type": "retrieval", "message": "Found 5 relevant passages"}

event: sources
data: {"sources": [{"name": "doc.pdf", "url": "https://...", "snippet": "..."}]}

event: delta
data: {"choices": [{"delta": {"content": "Based on the documents..."}}]}

event: delta
data: {"choices": [{"delta": {"content": " the answer is..."}}]}

event: done
data: {"usage": {"prompt_tokens": 450, "completion_tokens": 120}, "conversation_id": "..."}
```

**Current gap:** In `streaming_chat_response_handler()` at `middleware.py:3267`, the SSE passthrough path (Path B, line 4851) only wraps the raw LLM stream. Status events, source events, and citation events are emitted via `event_emitter()` which only targets Socket.IO.

**Fix approach:** Create an SSE-compatible event emitter that:
1. Wraps the same events that `event_emitter()` sends via Socket.IO
2. Prepends them to the SSE stream as typed events (`event: status`, `event: sources`, etc.)
3. Falls through to the LLM token stream for `delta` events

This is a modification to `streaming_chat_response_handler()` to detect the "no WebSocket" case and use an SSE event accumulator instead of dropping status events.

**Implementation detail:** The `event_emitter` function is created in `socket/main.py:779-909`. For the SSE path, we'd create an alternative `sse_event_emitter` that appends events to an async queue. The stream generator yields from this queue interleaved with the LLM stream.

#### Component 3: Documentation & SDK Helpers (~2-3 days)

- OpenAPI documentation for the query endpoint (FastAPI generates this automatically)
- Example Python client using `httpx` with SSE parsing
- Example JavaScript/TypeScript client using `fetch` with `ReadableStream`
- Authentication guide (API key creation, endpoint restrictions)
- Rate limiting recommendations (currently none exist for API keys)

#### Component 4: Agent Support (Future, TBD)

When custom soev.ai agents are implemented, the query endpoint can accept an `agent_id` parameter:

```python
class QueryRequest(BaseModel):
    ...
    agent_id: Optional[str] = None     # Use a specific agent
```

The agent would determine: model selection, system prompt, available tools, and retrieval strategy. This is additive — the base query endpoint works without agents.

### Architecture Overview

```
External Party                        Open WebUI
─────────────                         ──────────

1. Upload docs ──→ POST /integrations/ingest ──→ KB + Vector DB
                   (already exists)

2. Query KB ────→ POST /integrations/query ──→ process_chat_payload()
                   (NEW endpoint)                  │
                                                   ├─→ RAG retrieval
                                                   ├─→ Source context injection
                                                   └─→ LLM completion
                                                        │
3. SSE stream ←── text/event-stream ←──────────────────┘
                   status → sources → deltas → done
```

### Security Considerations

1. **Endpoint restrictions**: Use `API_KEYS_ALLOWED_ENDPOINTS` to limit service account keys to only `/api/v1/integrations/*` endpoints, preventing access to admin/user management APIs.

2. **KB scoping**: The query endpoint should verify the KB belongs to the authenticated provider (same check as ingest: `knowledge.type == provider_slug`). External parties can only query their own KBs.

3. **Model access**: Either use a fixed model from provider config, or validate the requested model against an allowlist per provider.

4. **Rate limiting**: Currently no rate limiting on API keys. Should add per-provider rate limits (requests/minute, tokens/day) before exposing to external parties. The existing `RateLimiter` class (`utils/rate_limit.py`) with Redis support can be reused.

5. **Token usage tracking**: Add per-provider usage logging for billing/monitoring. The LLM response includes `usage` data that should be recorded.

### Comparison: Our Approach vs Alternatives

| Approach | Pros | Cons |
|----------|------|------|
| **New `/integrations/query` endpoint** (recommended) | Clean contract, scoped to provider's KBs, simple for consumers | New endpoint to maintain |
| **Expose existing `/api/chat/completions`** | Zero new code | Complex payload, requires chat session management, no KB scoping |
| **OpenAI-compatible wrapper** | Familiar to developers | Doesn't map well to RAG + sources, loses rich events |
| **Anthropic Messages API** (`/api/v1/messages`) | Already exists in codebase | Anthropic-specific format, limited adoption |

### Effort Breakdown

#### Phase 1: Stateless Query Endpoint (~1 week)

| Task | Effort | Details |
|------|--------|---------|
| `QueryRequest`/`QueryResponse` models | 2h | Pydantic models with validation |
| Query endpoint implementation | 1d | KB resolution, message building, `process_chat()` call |
| Conversation management | 1d | Create/retrieve chat for multi-turn, store messages |
| SSE stream wrapping | 1d | Async generator yielding SSE-formatted events |
| Provider-scoped KB access check | 2h | Verify KB type matches provider |
| Error handling | 4h | Structured error events in SSE, graceful failures |
| Unit tests | 1d | Endpoint tests, auth tests, streaming tests |

#### Phase 2: Rich SSE Events (~3-4 days)

| Task | Effort | Details |
|------|--------|---------|
| SSE event emitter | 1d | Alternative to WebSocket `event_emitter` |
| Wire status events | 4h | RAG progress, query generation |
| Wire source/citation events | 4h | Retrieved sources with snippets |
| Wire error events | 2h | LLM errors, timeout, model unavailable |
| Integration tests | 1d | End-to-end SSE stream validation |

#### Phase 3: Documentation (~2-3 days)

| Task | Effort | Details |
|------|--------|---------|
| API documentation | 4h | Endpoint docs with examples |
| Python example client | 4h | Using httpx + SSE |
| JS example client | 4h | Using fetch + ReadableStream |
| Auth & setup guide | 2h | Service account, API key, config |

**Total: ~2-3 weeks**, assuming one developer.

### Feasibility Assessment

**High feasibility.** The core infrastructure (RAG pipeline, LLM routing, streaming, auth) all exist and are well-tested. The main work is:

1. **A thin API layer** translating the external contract to internal structures
2. **SSE event piping** to surface events that currently only go through WebSocket
3. **Provider scoping** to ensure KB access control

**Risks:**
- The `process_chat_payload` middleware is large (~600 lines) and tightly coupled to the frontend's expectations. The query endpoint needs to construct `form_data` that satisfies all the middleware's assumptions.
- SSE error handling: if the LLM stream fails mid-response, we need to send a clean error event before closing the stream.
- Multi-turn conversation state: the existing Chat model stores a lot of metadata (messages, tags, output items). The external API needs a lighter interface that doesn't expose internal structures.

**Mitigations:**
- The middleware is well-documented via prior research. The critical fields are known (see message format in chat research).
- Error events can reuse the existing `chat:message:error` event type.
- The Chat model can be reused as-is; we just don't expose its internal fields in the API response.

## Code References

- `backend/open_webui/main.py:1922` — Chat completions endpoint entry point
- `backend/open_webui/main.py:2185-2202` — WebSocket vs direct dispatch decision
- `backend/open_webui/utils/middleware.py:2146` — `process_chat_payload()` RAG pipeline
- `backend/open_webui/utils/middleware.py:3267` — `streaming_chat_response_handler()` dual path
- `backend/open_webui/utils/middleware.py:4851` — SSE passthrough path (Path B)
- `backend/open_webui/socket/main.py:779-909` — `event_emitter()` WebSocket events
- `backend/open_webui/routers/integrations.py:453` — Push ingest endpoint
- `backend/open_webui/routers/integrations.py:74-94` — `get_integration_provider()` auth check
- `backend/open_webui/utils/auth.py:275-412` — `get_current_user()` with API key support
- `backend/open_webui/config.py:3484` — `INTEGRATION_PROVIDERS` config
- `backend/open_webui/utils/rate_limit.py` — `RateLimiter` class for future rate limiting

## Historical Context

- `thoughts/shared/research/2026-03-15-push-ingest-integration.md` — Push integration design with provider registry
- `thoughts/shared/research/2026-03-18-generic-push-interface-design.md` — Generic push interface supporting all data types
- `thoughts/shared/research/2026-03-06-external-data-pipeline-ingestion.md` — Original external pipeline research
- `thoughts/shared/research/2026-03-13-airweave-vs-custom-integration-comparison.md` — Decision to build custom integration

## Related Research

- `thoughts/shared/research/2026-03-15-push-ingest-integration.md` — Foundation for the upload side
- `thoughts/shared/research/2026-03-18-generic-push-interface-design.md` — Data type handling for push

## Open Questions

1. **Should the query endpoint create persistent chats?** Or use ephemeral in-memory conversation state? Persistent chats allow admins to audit external queries but add storage overhead. Recommendation: persistent, with a configurable TTL for cleanup.

2. **Model selection strategy**: Should external parties choose their model, or should the provider config specify a fixed model? Per-provider fixed model is simpler and avoids cost surprises. Allow override only if explicitly configured.

3. **Concurrent query limits**: Should we limit how many concurrent queries a provider can run? The LLM is the bottleneck. A per-provider semaphore would prevent one client from starving others.

4. **Response format for non-streaming**: When `stream=false`, should we return OpenAI-compatible JSON or a custom format that includes sources? Recommendation: custom format with sources — external parties need the citations.

5. **Agent integration timeline**: The custom soev.ai agents will significantly increase the value of this API. Should we wait for agents, or ship the base query endpoint first? Recommendation: ship base first, add agent support as an additive parameter.

6. **Webhook for long-running queries**: If an LLM response takes >30s (complex RAG + reasoning), should we support an async webhook callback pattern in addition to SSE?
