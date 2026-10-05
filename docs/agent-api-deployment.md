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

## Title and tag generation under the v2 runtime

Titles, tags and follow-ups are Open WebUI tasks: a plain chat completion on the task model, outside the agent. Under the v2 runtime (`AGENT_API_RUNTIME=v2`), the model connection is soev-api's `/v1/chat`. It lists the models but serves only threads, so a task sent there returns `404 route_not_found` and the chat keeps the first words of the prompt as its title.

When no task model is set, or the one set is not on any connection, the task falls back to the chat model, which is on soev-api. Give Open WebUI a second connection straight to inference (LiteLLM in a cluster) and point the external task model at a model on it:

```env
OPENAI_API_BASE_URLS=http://soev-api/v1/chat;http://litellm-proxy.shared-services.svc:4000/v1
OPENAI_API_KEYS=<soev-api key>;<litellm key>
OPENAI_API_CONFIGS={"0": {"enable": true}, "1": {"enable": true, "model_ids": ["google/gemma-4-31B-it"]}}
TASK_MODEL_EXTERNAL=google/gemma-4-31B-it
```

The task model then also appears in the model picker. Hide it under Admin > Models; a hidden model still serves tasks. Hiding is stored on the model in the database and has no environment variable.

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

## Migrating a v1 tenant to v2

`python -m open_webui.soev.migrate` moves an existing tenant (own Weaviate, a direct LiteLLM connection, local KBs) to v2 (soev-api KBs in pgvector, models from the soev-api catalog, the agent through soev-api). The chart runs it as a Job when `soevApi.migrate.enabled` (see `helm/open-webui-tenant/README.md`). It needs the v2 image with O4: the switch retires the OpenAI connection, so models must come from soev-api's catalog.

Inputs (environment):

- `SOEV_V2_MIGRATION_ID`: names the run. The snapshot, the once-only steps and the sign-out are recorded per id.
- `SOEV_V2_CONFIG`: JSON with exactly `agent_api.selected_agent`, `document_writer.enable`, `live_documents.enable`, `live_mail.enable`, `notes.enable`, `web.search.enable`, `webui.url` and `user.permissions.features` (merged into `user.permissions`).
- `SOEV_V2_MODEL_MAP`: JSON of LiteLLM model name to catalog id, e.g. `{"zai-org/GLM-5.3": "glm-5-3"}`. soev-api serves no `/v1/models` with the registry `model_string` yet, so the pairs come from the catalog by hand.
- `SOEV_V2_INGEST_CONCURRENCY` (default 4) and `SOEV_V2_WAIT_SECONDS` (default 0; the chart sets 600).

`--apply` runs these steps in order; each is safe to rerun after a partial failure:

1. **Snapshot.** The config rows the next steps change are kept as stored text, once per migration id. The migration's state (snapshot, model id records, step markers) lives outside Alembic, in schema `owui_v2_migration` on PostgreSQL (`owui_v2_migration_*` tables on SQLite), so the database stays at the v1 Alembic head.
2. **Config switch** (once per id). The OpenAI and Ollama connections, `ui.default_models` and the task models are retired; the `SOEV_V2_CONFIG` values are written.
3. **Model ids** (once per id). LiteLLM names become catalog ids in message model ids, chat JSON, assistant base models, base-model override rows and their grants, user default and pinned models, automations, `ui.default_pinned_models` and `ui.model_order_list`. Every change is recorded with its path. Unmapped ids are listed and left as they are.
4. **Directory and KB copy.** Identity links, groups, collections with grants, folders and cloud schedules.
5. **Re-ingest.** Each file of a local KB without a soev document is submitted through `ingest.submit`. Cloud KBs re-sync from their schedules. The job outcome is written to the file row (processing, then completed or failed), which v1 shares: after a rollback, v1 shows the v2 outcome on re-ingested files, e.g. failed for a file v2 could not ingest, while its own v1 vectors are intact.
6. **Memories.** Users whose vectors do not match their memory rows are re-embedded with the app's embedding function.
7. **Reconcile.** Exit 0 when every file is ingested or terminally failed (listed); 75 while ingest jobs still run; 1 on a missing or conflicting collection, missing memory vectors or an unreachable soev-api; 2 on invalid input.
8. **Sign-out** (once per id, after exit 0). Every user's tokens are revoked through the Redis `revoked_at` marker, so each first Microsoft login creates the proven Entra link soev-connect requires. `WEBUI_SECRET_KEY` is not rotated. The sign-out is best-effort: if Redis loses its state, a user keeps their session, gets "Log in again with Microsoft to connect" (shown whenever soev-api answers `subject_not_linked`) on their first consent, and the link forms at that login. Being signed out is a convenience, not a security control.

`--dry-run` prints the plan without writes or HTTP requests (it does not create the state tables either), and needs the same `SOEV_V2_*` inputs as `--apply`. The CLI requires one of `--apply`, `--restore` or `--dry-run`; it no longer runs the directory copy alone. The plan shows the snapshot state, every switched key, the model mapping with counts and unmapped ids, per-KB files to check, memory rows and the sign-out.

`--restore` puts the snapshotted config rows back byte for byte (and removes keys that had no row), and puts back every recorded model id that still holds the value written; references changed since are listed and left. Messages added after the cutover stay. soev-api data and the `owui_v2_migration` state are not touched; the state stays for audit until the cleanup PR drops it. A later `--apply` with the same id switches again from the kept snapshot but does not sign users out again.

Rollback procedure:

1. Set `soevApi.migrate.mode: restore`. The new Job restores the config rows and model ids.
2. Then revert the image and values to v1. The database is still at the v1 Alembic head, so the v1 image's schema upgrade runs as before.

Not migrated: Confluence KBs (Confluence is out of scope), web-search result collections (transient), Weaviate vectors (re-embedded from the originals), and model ids in feedback records. Proven Entra links are never backfilled; they form at login.
