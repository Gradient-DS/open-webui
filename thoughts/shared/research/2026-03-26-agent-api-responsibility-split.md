---
date: 2026-03-26T16:00:00+02:00
researcher: Claude Code
git_commit: c0db5407764a3c167d1c971ee01b26284e7c2e28
branch: dev
repository: Gradient-DS/open-webui
topic: "Agent API Responsibility Split: What the Agent Handles vs Open WebUI"
tags: [research, codebase, agent-api, middleware, rag, web-search, title-generation, memory]
status: complete
last_updated: 2026-03-26
last_updated_by: Claude Code
---

# Research: Agent API Responsibility Split — What the Agent Handles vs Open WebUI

**Date**: 2026-03-26
**Researcher**: Claude Code
**Git Commit**: c0db5407764a3c167d1c971ee01b26284e7c2e28
**Branch**: dev
**Repository**: Gradient-DS/open-webui

## Research Question

When `AGENT_API_ENABLED` is activated, what functions are handled by the external agent API, what is still managed by Open WebUI, and where do these overlap or combine?

## Summary

When `AGENT_API_ENABLED=true`, the **chat completion** is routed to an external agent service. The agent takes over **RAG/knowledge retrieval**, **web search**, and **tool orchestration/resolution**. However, Open WebUI still fully manages **title generation**, **tag generation**, **follow-up generation**, **memory retrieval/injection**, **image generation prompts**, **code interpreter prompts**, **skills injection**, **chat persistence**, **WebSocket streaming**, and **voice mode prompts**. Memory injection is a notable overlap — Open WebUI injects memories into the messages before the agent sees them, yet also passes the `memory` feature flag to the agent.

## Detailed Findings

### What the Agent API Takes Over (bypassed in Open WebUI)

The `AGENT_API_ENABLED` flag gates three sections in `process_chat_payload()` in `middleware.py`:

#### 1. Knowledge/RAG Retrieval (line 2271-2274)
Open WebUI **skips** built-in knowledge flattening and RAG context injection. Raw KB references (id, name, type, collection_names) are passed to the agent via `metadata["knowledge"]`.

#### 2. Web Search (line 2366-2375)
Open WebUI **skips** the built-in `chat_web_search_handler()`. The agent receives the `web_search` feature flag in its payload and handles search itself.

#### 3. Tool Resolution + RAG File Processing + Context Injection (line 2495-2502)
The main bypass — the entire block that resolves tools, processes file attachments into RAG context, and builds the final prompt is **skipped**. The agent receives raw metadata (files, tool_ids, features, knowledge) and makes its own decisions.

#### 4. LLM Routing (main.py:2095-2105)
Instead of calling `chat_completion_handler` → Ollama/OpenAI, the request goes to `call_agent_api()` → `AGENT_API_BASE_URL/v1/chat/completions`.

### What Open WebUI Still Manages (NOT bypassed)

These all run **regardless** of `AGENT_API_ENABLED`:

| Feature | Phase | Where |
|---------|-------|-------|
| **Memory retrieval/injection** | Pre-request | `middleware.py:2359-2364` |
| **Voice mode prompt** | Pre-request | `middleware.py:2347-2357` |
| **Image generation prompt injection** | Pre-request | `middleware.py:2377-2382` |
| **Code interpreter prompt injection** | Pre-request | `middleware.py:2384-2413` |
| **Skills injection** | Pre-request | `middleware.py:2423-2461` |
| **File/folder expansion** | Pre-request | `middleware.py:2469-2485` |
| **Pipeline inlet/outlet** | Pre/post-request | `middleware.py` |
| **Access control** | Pre-request | `main.py:1944-1951` |
| **Title generation** | Post-response | `middleware.py:3022-3088` |
| **Tag generation** | Post-response | `middleware.py:3090-3130` |
| **Follow-up generation** | Post-response | `middleware.py:~2970-3017` |
| **Chat persistence (DB)** | Post-response | `main.py:2106-2115` |
| **WebSocket streaming** | During response | via `process_chat_response` |

### Key Insight: Memory Is NOT Skipped

Memory retrieval (`chat_memory_handler` at line 2359) runs **before** the `AGENT_API_ENABLED` early-return at line 2500. It's only gated by `features["memory"]` and `function_calling != "native"`.

This means:
- Open WebUI queries its memory store and **injects relevant memories into the system message** before sending to the agent
- The agent receives messages that already contain memory context
- The agent also receives `features: {"memory": true}` in its payload
- If the agent has its own memory system, there could be duplicate context

### Title Generation — Always Open WebUI

Title generation (`middleware.py:3022-3088`) happens in the **post-response** phase via `generate_title()` which calls the configured task model (usually the same LLM, via standard OpenAI/Ollama routing — **not** through the agent API). The agent has no mechanism to generate or influence titles.

Same applies to **tag generation** and **follow-up suggestion generation**.

### Data Flow Comparison

**Without Agent API (standard flow):**
```
Request → Pipeline Inlet → Memory Injection → Web Search → Image Gen → Code Interpreter
→ Skills → Tool Resolution → RAG File Processing → Context Injection
→ LLM Provider (Ollama/OpenAI) → process_chat_response
→ Title/Tag/Follow-up Generation → Pipeline Outlet → DB Save
```

**With Agent API enabled:**
```
Request → Pipeline Inlet → Memory Injection → (web search SKIPPED)
→ Image Gen → Code Interpreter → Skills
→ (tool resolution SKIPPED) → (RAG processing SKIPPED) → (context injection SKIPPED)
→ Agent API (receives raw files, knowledge, tool_ids, features)
→ process_chat_response (identical streaming/persistence)
→ Title/Tag/Follow-up Generation → Pipeline Outlet → DB Save
```

### What the Agent Receives

The agent receives this payload via `AgentPayload` (`utils/agent.py:55-77`):

| Field | Source | Notes |
|-------|--------|-------|
| `agent` | `AGENT_API_AGENT` env var | Which agent to invoke |
| `model` | Selected model ID | |
| `messages` | Full message history | **Memory already injected by Open WebUI** |
| `features` | Feature flags dict | `{web_search: true, memory: true, ...}` |
| `files` | Attached file metadata | Raw, not yet processed into RAG context |
| `knowledge` | Raw KB references | `[{id, name, type, collection_names}]` |
| `tool_ids` | Configured tool IDs | Not resolved, just IDs |
| `rag_filter` | RAG filter config | |
| `temperature`, etc. | Model parameters | |

### SSE Protocol from Agent

The agent returns a standard SSE stream with custom events:
- `event: status` → routed to Socket.IO (shows "Searching..." etc.)
- `event: source` → routed to Socket.IO (renders citation chips)
- Standard `data:` lines → passed through to `process_chat_response` (token streaming)

### Two Distinct "External Agent" Systems

There are **two separate systems** with confusingly similar names:

1. **`AGENT_API_ENABLED`** (`env.py:873`, `utils/agent.py`) — Routes the chat completion to an external service, bypassing built-in retrieval/tools. Configured via `AGENT_API_BASE_URL` and `AGENT_API_AGENT`.

2. **`external_agents.py`** (`utils/external_agents.py`) — Loads Pipe functions from external repos into the Functions table at startup. These run through the **normal** Open WebUI middleware pipeline (no bypassing). Configured via `EXTERNAL_AGENTS_REPO`, `EXTERNAL_AGENTS_PACKAGE`, `EXTERNAL_AGENTS_LIST`.

## Overlap & Gap Analysis

| Concern | Open WebUI | Agent API | Overlap? |
|---------|------------|-----------|----------|
| **RAG / Knowledge retrieval** | Skipped | Agent handles | Clean handoff ✅ |
| **Web search** | Skipped | Agent handles | Clean handoff ✅ |
| **Tool resolution & calling** | Skipped | Agent handles | Clean handoff ✅ |
| **Memory retrieval** | Injects into messages | Receives `memory: true` flag | **Overlap** ⚠️ |
| **Title generation** | Always runs | No mechanism | Open WebUI only |
| **Tag generation** | Always runs | No mechanism | Open WebUI only |
| **Follow-up generation** | Always runs | No mechanism | Open WebUI only |
| **Image gen prompt** | Always runs | Not involved | Open WebUI only |
| **Code interpreter prompt** | Always runs | Not involved | Open WebUI only |
| **Voice mode prompt** | Always runs | Not involved | Open WebUI only |
| **Skills** | Always injected | Not involved | Open WebUI only |
| **Chat persistence** | Always | Never | Open WebUI only |

## Code References

- `backend/open_webui/env.py:873-875` — `AGENT_API_ENABLED`, `AGENT_API_BASE_URL`, `AGENT_API_AGENT`
- `backend/open_webui/utils/agent.py` — Transport client + integration glue (`call_agent_api`)
- `backend/open_webui/utils/middleware.py:2274` — Knowledge bypass
- `backend/open_webui/utils/middleware.py:2371` — Web search bypass
- `backend/open_webui/utils/middleware.py:2500-2502` — Main bypass (tools, RAG, context injection)
- `backend/open_webui/utils/middleware.py:2359-2364` — Memory injection (NOT bypassed)
- `backend/open_webui/utils/middleware.py:3022-3130` — Title/tag generation (NOT bypassed)
- `backend/open_webui/main.py:2095-2105` — LLM routing to agent API
- `backend/open_webui/utils/external_agents.py` — Separate Pipe function loader (unrelated)

## Open Questions

1. **Should memory injection be skipped** when `AGENT_API_ENABLED` is true? The agent could handle its own memory if it receives the feature flag. Currently there's a risk of duplicate memory context.
2. **Should title/tag generation be delegable** to the agent API? Currently the agent can't influence these.
3. **Should image gen / code interpreter prompt injection be skipped?** These inject prompts into messages that the agent then receives — the agent may have its own prompt strategy.
4. **The agent doesn't know that memory was already injected.** Should there be a signal (e.g., `features.memory_pre_injected: true`) so the agent can skip its own memory lookup?
5. **Skills injection still runs** — should the agent receive raw skill IDs instead, similar to how it receives raw tool_ids?
