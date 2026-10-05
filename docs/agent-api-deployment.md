# Agent API Integration — Staging Deployment Guide

This documents how to enable the Gradient Agent API integration in OpenWebUI. When enabled, OpenWebUI routes chat completions to an external agent service instead of handling web search, RAG, and LLM orchestration itself.

When disabled (the default), OpenWebUI behaves as stock.

## Environment Variables

Add these to your `.env` (or set them in the container environment):

```env
# Master toggle — must be "true" to enable (default: false)
AGENT_API_ENABLED=true

# Base URL of the agent service (no trailing slash)
AGENT_API_BASE_URL=http://agent-service:8001

# API key the agent service accepts on the X-API-Key header
AGENT_API_KEY=dev-api-key

# Optional comma-separated list of agents the admin can pick from in
# Admin Settings > External Agents. When empty, the agents service uses
# its own default_agent.
AGENT_API_AGENTS=agent-one,agent-two
```

| Variable             | Required           | Default | Description                                                                               |
| -------------------- | ------------------ | ------- | ----------------------------------------------------------------------------------------- |
| `AGENT_API_ENABLED`  | Yes                | `false` | Enables/disables the integration. When `false` or unset, all behavior is stock OpenWebUI. |
| `AGENT_API_BASE_URL` | Yes (when enabled) | `""`    | The agent service URL. OpenWebUI POSTs to `{base_url}/v1/chat/completions`.               |
| `AGENT_API_KEY`      | Yes (when enabled) | `""`    | API key forwarded as the `X-API-Key` header. Must match `AGENTS_API_KEY` on the agent service. |
| `AGENT_API_AGENTS`   | No                 | `""`    | Comma-separated list of agent identifiers offered in the admin "External Agents" tab. When empty, the agents service falls back to its configured `default_agent`. When set, the admin's selection is forwarded as an override hint on `AgentPayload.agent`. |

By default the agent (persona) is selected server-side via `default_agent` in the agent service config. Deployments that expose multiple agents behind one `AGENT_API_KEY` can optionally use `AGENT_API_AGENTS` + the "External Agents" admin tab to let admins switch without a redeploy.

## What Changes When Enabled

| Capability                | Stock OpenWebUI                                    | With Agent API                                           |
| ------------------------- | -------------------------------------------------- | -------------------------------------------------------- |
| Web search                | Built-in handler runs                              | Skipped — agent handles its own search                   |
| RAG / knowledge retrieval | Built-in retrieval + "searching knowledge" spinner | Skipped — raw KB references passed to agent via metadata |
| LLM dispatch              | Calls OpenAI-compatible model endpoint             | Routes to agent API                                      |
| Tool resolution           | Built-in tool execution                            | Skipped — agent handles its own tools                    |

**Unchanged:** streaming to UI, DB persistence, title generation, WebSocket transport, system prompts, memory retrieval, voice mode.

## Models, default and tasks under the v2 runtime

v2 mode is on when `AGENT_API_ENABLED=true`, `AGENT_API_RUNTIME=v2` and `SOEV_API_URL` is set. soev-api then decides which models exist, which one is the default, and which model runs tasks:

- **Models.** The picker lists only soev-api's catalog (`GET /v1/models`, cached per process for 60 s). OpenAI and Ollama connections, function models and direct user connections are not offered, so `OPENAI_API_BASE_URLS` and connection `model_ids` have no effect. Access is unchanged: as with any base model, users see a catalog model only once an admin grants it under Admin > Models.
- **Default.** `/api/config` serves the catalog entry marked `default` as `default_models`. `ui.default_models` (`DEFAULT_MODELS`) is ignored. A model the user saved or picked still wins.
- **Tasks.** Titles, tags, follow-ups, emoji, queries, autocomplete, image prompts, MoA, context compaction, memory review and tool selection all go to `POST /v1/completions/task` as the acting user. soev-api runs them on the client's `task` model, so `TASK_MODEL`, `TASK_MODEL_EXTERNAL` and a direct LiteLLM connection are not needed. The prompt templates still come from Open WebUI.

Each catalog model carries its facts (vendor, origin, hosting, tri-state capabilities, lifecycle) under `info.meta.soev`. Vision counts as on only when the catalog says `supported`.

## Request Payload

OpenWebUI POSTs to `{AGENT_API_BASE_URL}/v1/chat/completions` with:

```json
{
	"model": "gpt-4o",
	"messages": [{ "role": "user", "content": "..." }],
	"stream": true,
	"chat_id": "uuid",
	"user_id": "uuid",
	"message_id": "uuid",
	"session_id": "socket-session-id",
	"features": { "web_search": true },
	"files": [
		{ "id": "file-uuid", "type": "file", "name": "report.pdf" },
		{ "id": "kb-uuid", "type": "collection", "name": "My KB" }
	],
	"knowledge": [{ "id": "kb-uuid", "type": "collection", "name": "My KB" }],
	"tool_ids": ["tool-1"],
	"temperature": 0.7
}
```

Key notes:

- `X-API-Key: {AGENT_API_KEY}` is sent on every request
- `model` is the user-selected LLM. The agent service validates it against its `available_llms` allowlist and returns 400 on an unknown ID — never a silent fallback.
- The agent (persona) is chosen server-side via `default_agent` in the agent service config. When `AGENT_API_AGENTS` is configured and an admin has picked one, OpenWebUI forwards the selection as an optional `agent` field for the service to honor as an override.
- `messages` already has the system prompt injected by OpenWebUI
- `features.web_search` indicates whether the user toggled web search on
- `files` contains all attached items (uploads + KB files)
- `knowledge` has raw KB references from the model config (id, name, type, collection_names)
- Model params (`temperature`, `top_p`, `max_tokens`, `frequency_penalty`, `presence_penalty`, `seed`, `stop`) are forwarded when set

## Expected Response Format

The agent must return a standard SSE stream (`Content-Type: text/event-stream`).

### Custom events (routed to UI via Socket.IO)

```
event: status
data: {"action": "knowledge_search", "description": "Searching knowledge base...", "done": false}

event: status
data: {"action": "knowledge_search", "done": true}

event: source
data: {"name": "report.pdf", "url": "..."}
```

- `event: status` — shows/clears spinners in the UI
- `event: source` — shows citation chips below the response

### Standard OpenAI chunks (streamed to the response body)

```
data: {"choices": [{"delta": {"content": "Hello"}, "index": 0}]}
data: {"choices": [{"delta": {"content": " world"}, "index": 0}]}
data: [DONE]
```

### Important

If the model has knowledge bases configured, the agent **must** emit a status event with `"done": true` to clear the "searching knowledge" spinner. Otherwise the spinner will hang in the UI.

## Agent Retrieval Callback

The agent can call back to OpenWebUI for vector search (handles embedding, hybrid search, and reranking automatically):

```
POST http://<openwebui-host>:8080/api/v1/retrieval/query/collection
Authorization: Bearer sk-<api_key>
Content-Type: application/json

{"collection_names": ["file-abc123", "kb-uuid"], "query": "search terms", "k": 5}
```

Generate an API key in OpenWebUI: **Settings > Account > API Keys**.

Vector DB collection name conventions:

- Uploaded files: `file-{file_id}`
- Knowledge bases: `{kb_id}` directly

## Verification

After deploying, verify the integration is working:

1. Check the env vars are set: the backend logs `AGENT_API_ENABLED`, `AGENT_API_BASE_URL`, and `AGENT_API_KEY` at startup
2. Send a chat message — it should hit `{AGENT_API_BASE_URL}/v1/chat/completions` instead of the model provider
3. Confirm status spinners and citation chips render in the UI
4. Confirm that setting `AGENT_API_ENABLED=false` (or removing it) restores stock behavior
