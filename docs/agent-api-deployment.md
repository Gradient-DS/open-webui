# Agent API deployment

Agent chat uses v2 exclusively. All tenants cut over in the same release; this
image has no legacy agent transport, discovery proxy or service-key retrieval
callbacks. Stock Open WebUI routing remains available when agent routing is disabled.

## Configuration

Set `AGENT_API_ENABLED=true` and configure:

| Environment variable | Chart value | Purpose |
| --- | --- | --- |
| `SOEV_API_URL` | `soevApi.url` | Platform base URL |
| `SOEV_API_KEY` | `secrets.soevApiKey` | Platform caller credential |
| `SOEV_API_SIGNING_KEY` | `secrets.soevApiSigningKey` | Unencrypted Ed25519 private PEM, preserving newlines |
| `SOEV_API_AUDIENCE` | `soevApi.audience` | Assertion audience |
| `SOEV_API_SERVICE_PRINCIPAL` | `soevApi.servicePrincipal` | Declared Open WebUI service principal |

The chart's routing switch is `openWebui.config.agentApiEnabled: "true"`.
With External Secrets enabled and a platform URL set, provide `soevApiKey` and
`soevApiSigningKey` in the configured 1Password item instead of inline secrets.
Startup validates both keys when the platform URL is set. Missing platform
configuration refuses agent requests; it does not select another transport.

Keep `AGENT_API_AGENTS`, `AGENT_API_AGENTS_CONFIG`,
`AGENT_API_AGENTS_CONFIG_OVERWRITE`, `FEATURE_AGENT_PICKER` and
`AGENT_API_PICKER_DEFAULT_SLUG` as needed. Their chart values under
`openWebui.config` remain `agentApiAgents`, `agentApiAgentsConfig`,
`agentApiAgentsConfigOverwrite`, `featureAgentPicker` and
`agentApiPickerDefaultSlug`. The admin-selected agent uses
`agent_api.selected_agent`; per-chat overrides take precedence. Existing rows
without v2 runtime metadata resolve to the configured deployment agent.

## Models, default and tasks under the v2 runtime

v2 mode is on when `AGENT_API_ENABLED=true` and `SOEV_API_URL` is set. soev-api then decides which models exist, which one is the default, and which model runs tasks:

- **Models.** The picker lists only soev-api's catalog (`GET /v1/models`, cached per process for 60 s). OpenAI and Ollama connections, function models and direct user connections are not offered, so `OPENAI_API_BASE_URLS` and connection `model_ids` have no effect. Access is unchanged: as with any base model, users see a catalog model only once an admin grants it under Admin > Models.
- **Default.** `/api/config` serves the catalog entry marked `default` as `default_models`. `ui.default_models` (`DEFAULT_MODELS`) is ignored. A model the user saved or picked still wins.
- **Tasks.** Titles, tags, follow-ups, emoji, queries, autocomplete, image prompts, MoA, context compaction, memory review and tool selection all go to `POST /v1/completions/task` as the acting user. soev-api runs them on the client's `task` model, so `TASK_MODEL`, `TASK_MODEL_EXTERNAL` and a direct LiteLLM connection are not needed. The prompt templates still come from Open WebUI.

Each catalog model carries its facts (vendor, origin, hosting, tri-state capabilities, lifecycle) under `info.meta.soev`. Vision counts as on only when the catalog says `supported`.

## Turn transport and verification

`utils/agent_v2.py` submits the new user input to platform-owned chat threads,
resumes or branches from stored turn positions, and translates platform events
into UI chunks, citations and Socket.IO updates. It supports streaming and
non-streaming responses through the same v2 implementation.

After deployment, verify the model catalog, a new turn, a follow-up and a
regenerated turn. Confirm citations, tool statuses and task-generated titles.
The discovery filter and agent-service welcome-message UI have been removed.

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
   **Chat uploads.** Every upload a stored chat attaches (chat-level and message `files`, type `file`, not an image, not picked from a KB; deleted chats excluded) that is not a KB file is submitted through `ingest.submit` as its owner into their attachments collection, the path a fresh v2 chat upload takes, and its `collection_name` is set to that collection. Files already there, in flight or terminally failed are skipped; an original gone from storage is listed and skipped. Images need nothing: the agent turn reads their bytes from storage. `--apply --uploads` (chart `mode: uploads`) runs only this step, for tenants migrated before it existed.
6. **Memories.** Users whose vectors do not match their memory rows are re-embedded with the app's embedding function.
7. **Reconcile.** Exit 0 when every file is ingested or terminally failed (listed); 75 while ingest jobs still run; 1 on a missing or conflicting collection, missing memory vectors or an unreachable soev-api; 2 on invalid input.
8. **Sign-out** (once per id, after exit 0). Every user's tokens are revoked through the Redis `revoked_at` marker, so each first Microsoft login creates the proven Entra link soev-connect requires. `WEBUI_SECRET_KEY` is not rotated. The sign-out is best-effort: if Redis loses its state, a user keeps their session, gets "Log in again with Microsoft to connect" (shown whenever soev-api answers `subject_not_linked`) on their first consent, and the link forms at that login. Being signed out is a convenience, not a security control.

`--dry-run` prints the plan without writes or HTTP requests (it does not create the state tables either), and needs the same `SOEV_V2_*` inputs as `--apply`. The CLI requires one of `--apply`, `--restore` or `--dry-run`; it no longer runs the directory copy alone. The plan shows the snapshot state, every switched key, the model mapping with counts and unmapped ids, per-KB files to check, memory rows and the sign-out.

`--restore` puts the snapshotted config rows back byte for byte (and removes keys that had no row), and puts back every recorded model id that still holds the value written; references changed since are listed and left. Messages added after the cutover stay. soev-api data and the `owui_v2_migration` state are not touched; the state stays for audit until the cleanup PR drops it. A later `--apply` with the same id switches again from the kept snapshot but does not sign users out again.

Rollback procedure:

1. Set `soevApi.migrate.mode: restore`. The new Job restores the config rows and model ids.
2. Then revert the image and values to v1. The database is still at the v1 Alembic head, so the v1 image's schema upgrade runs as before.

Not migrated: Confluence KBs (Confluence is out of scope), web-search result collections (transient), Weaviate vectors (re-embedded from the originals), and model ids in feedback records. Proven Entra links are never backfilled; they form at login.
