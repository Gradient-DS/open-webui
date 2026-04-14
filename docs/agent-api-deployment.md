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

# Which agent to route requests to
AGENT_API_AGENT=your-agent-name
```

| Variable             | Required           | Default | Description                                                                               |
| -------------------- | ------------------ | ------- | ----------------------------------------------------------------------------------------- |
| `AGENT_API_ENABLED`  | Yes                | `false` | Enables/disables the integration. When `false` or unset, all behavior is stock OpenWebUI. |
| `AGENT_API_BASE_URL` | Yes (when enabled) | `""`    | The agent service URL. OpenWebUI POSTs to `{base_url}/v1/chat/completions`.               |
| `AGENT_API_AGENT`    | Yes (when enabled) | `""`    | Agent identifier sent in each request payload.                                            |

## What Changes When Enabled

| Capability                | Stock OpenWebUI                                    | With Agent API                                           |
| ------------------------- | -------------------------------------------------- | -------------------------------------------------------- |
| Web search                | Built-in handler runs                              | Skipped — agent handles its own search                   |
| RAG / knowledge retrieval | Built-in retrieval + "searching knowledge" spinner | Skipped — raw KB references passed to agent via metadata |
| LLM dispatch              | Calls OpenAI-compatible model endpoint             | Routes to agent API                                      |
| Tool resolution           | Built-in tool execution                            | Skipped — agent handles its own tools                    |

**Unchanged:** streaming to UI, DB persistence, title generation, WebSocket transport, system prompts, memory retrieval, voice mode.

## Request Payload

OpenWebUI POSTs to `{AGENT_API_BASE_URL}/v1/chat/completions` with:

```json
{
	"agent": "your-agent-name",
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

1. Check the env vars are set: the backend logs `AGENT_API_ENABLED`, `AGENT_API_BASE_URL`, and `AGENT_API_AGENT` at startup
2. Send a chat message — it should hit `{AGENT_API_BASE_URL}/v1/chat/completions` instead of the model provider
3. Confirm status spinners and citation chips render in the UI
4. Confirm that setting `AGENT_API_ENABLED=false` (or removing it) restores stock behavior
