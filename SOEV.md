To start development:

(all in the root)

## Environment
copy .env.neo.example to .env
fill in at least an openai api key

## Services
docker compose -f docker-compose.neo-dev.yaml up -d

## Backend
python3.11 -m venv .venv
Activate venv

pip install -e ".[soev]"

open-webui dev (starts the backend server on port 8080)

## Frontend
nvm use 22  (Node must be <=22.x.x)
npm install --legacy-peer-deps
npm run dev
Should be available on localhost:5173

## Agent API (optional)

When enabled, OpenWebUI routes chat completions to an external agent service
instead of handling web search, RAG, and LLM orchestration itself.

### Setup

Add to your `.env`:
```
AGENT_API_ENABLED=true
AGENT_API_BASE_URL=http://localhost:8001
```

When `AGENT_API_ENABLED` is unset or `false`, OpenWebUI behaves as stock.

### What the agent API receives

OpenWebUI POSTs to `{AGENT_API_BASE_URL}/v1/chat/completions` with:

```json
{
  "model": "gpt-4o",
  "messages": [{"role": "user", "content": "..."}],
  "stream": true,
  "chat_id": "uuid",
  "user_id": "uuid",
  "message_id": "uuid",
  "session_id": "socket-session-id",
  "features": {"web_search": true},
  "files": [
    {"id": "file-uuid", "type": "file", "name": "report.pdf"},
    {"id": "kb-uuid", "type": "collection", "name": "My KB"}
  ],
  "knowledge": [{"id": "kb-uuid", "type": "collection", "name": "My KB"}],
  "tool_ids": ["tool-1"],
  "temperature": 0.7
}
```

- `messages` has the system prompt already injected
- `features.web_search` tells the agent whether the user toggled web search
- `files` contains all attached items (uploads + KB files). Vector DB collection
  names: `file-{id}` for files, `{id}` directly for knowledge bases
- `knowledge` has raw KB references from the model config

### Retrieval via OpenWebUI API

The agent can call back to OpenWebUI for vector search (handles embedding,
hybrid search, and reranking automatically):

```
POST http://localhost:8080/api/v1/retrieval/query/collection
Authorization: Bearer sk-<api_key>
Content-Type: application/json

{"collection_names": ["file-abc123", "kb-uuid"], "query": "...", "k": 5}
```

Generate an API key in OpenWebUI: Settings > Account > API Keys.

### What the agent API must return

A standard SSE stream (`Content-Type: text/event-stream`):

```
event: status
data: {"action": "knowledge_search", "description": "Searching...", "done": false}

event: status
data: {"action": "knowledge_search", "done": true}

event: source
data: {"name": "report.pdf"}

data: {"choices": [{"delta": {"content": "Hello"}, "index": 0}]}
data: {"choices": [{"delta": {"content": " world"}, "index": 0}]}
data: [DONE]
```

- `event: status` lines show spinners in the UI
- `event: source` lines show citation chips
- `data:` lines are standard OpenAI streaming chunks
- If the model has knowledge bases, the agent must emit
  `{"action": "knowledge_search", "done": true}` to clear the spinner

### What OpenWebUI still handles

Streaming to UI, DB persistence, title generation, WebSocket transport,
system prompts, memory retrieval, voice mode — all unchanged.