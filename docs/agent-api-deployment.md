# Agent API deployment

Agent chat uses v2 exclusively. All tenants cut over in the same release; this
image has no legacy agent transport, discovery proxy or service-key retrieval
callbacks. OpenAI-compatible provider connections, browser direct connections and remote
pipelines have been removed. Configure soev-api for the model catalog and tasks.

## Configuration

Set `AGENT_API_ENABLED=true` and configure:

| Environment variable | Chart value | Purpose |
| --- | --- | --- |
| `SOEV_API_URL` | `soevApi.url` | Platform base URL |
| `SOEV_API_KEY` | `secrets.soevApiKey` | Platform caller credential |
| `SOEV_API_SIGNING_KEY` | `secrets.soevApiSigningKey` | Legacy background assertions only: unencrypted Ed25519 private PEM, preserving newlines |
| `SOEV_API_AUDIENCE` | `soevApi.audience` | Legacy background assertion audience |
| `SOEV_API_SERVICE_PRINCIPAL` | `soevApi.servicePrincipal` | Declared Open WebUI service principal |

The chart's routing switch is `openWebui.config.agentApiEnabled: "true"`.
With External Secrets enabled and a platform URL set, provide `soevApiKey` and
`soevApiSigningKey` in the configured 1Password item instead of inline secrets.
Startup validates the product credential when the platform URL is set. Only legacy
background work loads the signing key, when it first signs. Missing platform
configuration refuses agent requests; it does not select another transport.

`AGENT_API_AGENTS` (chart `agentApiAgents`) lists the agents an admin can
select in the External Agents tab; the selection (`agent_api.selected_agent`)
is sent with every turn. There is no per-chat agent: with the agent API on,
every chat goes to the agent service. A `chat.meta.agent_id` left by the
retired v1 picker is ignored.

## Keycloak login

Use the generic OIDC provider for the tenant's Keycloak realm:

```env
ENABLE_OAUTH_SIGNUP=true
OAUTH_PROVIDER_NAME=Keycloak
OPENID_PROVIDER_URL=https://login.example.invalid/realms/fictional-tenant/.well-known/openid-configuration
OAUTH_CLIENT_ID=fictional-owui-client
OAUTH_CLIENT_SECRET=<client secret>
OAUTH_SCOPES=openid email profile
ENABLE_LOGIN_FORM=false
```

Set the client secret through the deployment's secret store. Password login takes
place only through Keycloak; OWUI's login form stays disabled. Existing users must
have their OIDC identity mapping provisioned by the migration; matching email
addresses do not link accounts.

For authenticated requests with an owned `oidc` OAuth session, OWUI refreshes the
access token when needed and forwards it as `X-Soev-User-Token` beside the product
credential in `Authorization`. It never sends `X-Soev-Subject` with a login token.
A user operation without that person's token fails with `401` / `sign_in`:
password users, other OAuth providers, OWUI API keys, missing or expired sessions,
and attempts to act as another user do not fall back to an assertion. Chat tasks
started by a user request inherit its token context. Document downloads follow
one redirect to `/v1/downloads/{code}` on the configured soev-api origin with the
same product credential and login token, without `X-Soev-Subject`. Preview requests
preserve Range and partial responses. File uploads stream through authenticated
`PUT /v1/jobs/{job_id}/uploads/{source_id}` with bounded reads; browsers receive no
object-store URL. Only raster images (PNG, JPEG, GIF, WebP) and PDF may render inline;
SVG and other types download as attachments, and responses carry `nosniff`.

Admin suspension to `pending` is refused with `403 policy_forbids` /
`suspension:console` whenever soev is configured, before remote calls or local
writes. v2 offboarding keeps the same console-only boundary. OWUI never revokes
the target person's schedules or connections on an admin's behalf.
Archive DELETE and retention PATCH require the admin's own login token and use
a requester client. Scheduled archive expiry remains tokenless and makes no
platform calls; the platform owns remote expiry.

`SoevClient` resolves the requesting user's token even when a caller omits an
explicit user reference. `as_service()` / `build_service_client()` explicitly
select product authority for catalog/capability discovery, directory/group pushes,
service inventory and grant projections, collection ownership
operations, and credential-scoped job reads/commits. Those calls send neither
person header. ThreadClient already requires the requester's token.

### Remaining legacy subject assertions

Only `identity.build_background_client()` constructs `BackgroundSoevClient`.
It refuses a user request context or an explicit login token before minting.
`identity.mint_assertion` also refuses request context, including legacy link
assertions in JSON bodies. The remaining subject-header callers are listed
exhaustively below; paths are under `backend/open_webui/soev/`.

| Caller | Subject-header operations | Why no login token exists |
| --- | --- | --- |
| `migrate.copy_directory → _create_collection` | Create a collection when its migration supplies an owner | Offline migration command |
| `migrate.copy_directory → _create_cloud_sync` | Create the owner's provider connection | Offline migration command |
| `migrate.copy_directory → _subscribe` | Create the owner's content schedules | Offline migration command |
| `migrate.copy_directory → _cloud_coverage` | Page the owner's schedules | Offline reconciliation |
| `migrate.copy_directory → _reconcile_collections` | Page each cloud owner's collections | Offline reconciliation; cloud content is not a service-reader inventory |
| `migrate_ingest.reingest → ingest.submit` | Create and commit file jobs using `directory.client` from `migrate.copy_directory` | Offline re-ingest as the recorded KB owner |
| `migrate_uploads.ingest_uploads → _ensure → ingest.ensure_attachments_collection` | Read/create/tag the owner's attachment collection | Offline chat/folder upload migration |
| `migrate_uploads.ingest_uploads → _ensure → assistant_files.ensure_collection` | Read/create/regrant the assistant owner's collection | Offline assistant upload migration |
| `migrate_uploads.ingest_uploads → _submit → ingest.submit` | Create and commit upload jobs | Offline upload migration |
| `jobs._tick → poll_once → _reference_ready` | Read a completed reference document as its File owner to fill missing MIME metadata | Durable job polling runs without a user session; existing D-2g-4 deferral |

`identity.ensure_link` remains only for `migrate._push_directory`,
`migrate_uploads._ensure`, and `jobs._reference_ready`. These send a signed
assertion in a link request's body using product authority, without a subject
header. User requests no longer publish links or proven Entra assertions; approval
link retries are removed. Ordinary request clients do not need a signing key.

The coordinated platform cut removes these background assertions and legacy link
operations. This OWUI change leaves their tokenless offline/background behavior
explicit until their replacement lands.

## Optional platform capabilities

The fork reads `GET /v1/capabilities` with its existing API credential at startup
and on demand. The response is `{ "contract": "0.3.0", "features": ["..."] }`.
Successful reads are cached per process for 60 seconds. An unavailable or malformed
endpoint retains the last successful feature set for that backend, logs once per
outage, and retries after 10 seconds. Until the first successful read, optional
features remain disabled. Changing the backend URL or API key resets the cache.

| Feature name | Controls |
| --- | --- |
| `live_documents` | OneDrive live search and explicit picker attachments |
| `live_mail` | Outlook mail search and reads |
| `cloud_sync` | Bulk sync policy, schedules, usage and sync actions |
| `meetings` | Meeting routes, UI availability and meeting attachments |
| `groups_push` | Publishing local group memberships to the platform directory |
| `images` | Chat image upload and the catalog's UI vision availability |

The existing deployment and user settings still apply. Capabilities narrow their
availability without changing saved preferences. Connection and consent routes
are shared by `cloud_sync`, `live_documents` and `live_mail`; any of these enables
those routes, while each live family and bulk sync requires its own capability.
Model discovery, identity, ordinary ingestion, durable job polling and knowledge
access still depend on the configured backend URL. An open soev-server advertising
`features: []` can serve core chat and models without optional platform calls.

## Models, default and tasks under the v2 runtime

v2 mode is on when `AGENT_API_ENABLED=true` and `SOEV_API_URL` is set. soev-api then decides which models exist, which one is the default, and which model runs tasks:

- **Models.** The picker lists only soev-api's catalog (`GET /v1/models`, cached per process for 60 s). Ollama and function models are not offered. OpenAI-compatible and browser direct connections no longer exist. Access is unchanged: as with any base model, users see a catalog model only once an admin grants it under Admin > Models.
- **Default.** `/api/config` serves the catalog entry marked `default` as `default_models`. `ui.default_models` (`DEFAULT_MODELS`) is ignored. A model the user saved or picked still wins.
- **Tasks.** Titles, tags, follow-ups, emoji, queries, autocomplete, image prompts, MoA, context compaction, memory review and tool selection all go to `POST /v1/completions/task` as the acting user. soev-api runs them on the client's `task` model, so `TASK_MODEL`, `TASK_MODEL_EXTERNAL` and a direct LiteLLM connection are not needed. The prompt templates still come from Open WebUI.
- **Memory embeddings.** Add, query, update and reindex use `POST /v1/embeddings` through `SoevClient`, with the product credential and requesting person's token. soev-api selects its embedding role; the runtime ignores OWUI's RAG embedding model, connection and prefixes for memories. Admin reindex authenticates as the requesting admin. Memory vectors still live in the existing per-user vector collections; this change does not migrate them or change their model automatically.
- **Dictation.** `POST /api/v1/audio/transcriptions` sends the uploaded file, optional language and `response_format=json` to soev-api's `/v1/audio/transcriptions`. The platform selects its transcription role and enforces audio format and duration; OWUI bounds forwarding to 25 MiB. Voice and user STT permissions still apply. Dictation has no provider settings or local Whisper fallback and ignores saved browser-engine overrides. The admin provider settings and user engine selector are hidden. OWUI creates no transcription cache files on this path. Both primitives propagate platform failures through the usual safe nl/en error handler, without a direct inference fallback.

Each catalog model carries its facts (vendor, origin, hosting, tri-state capabilities, lifecycle) under `info.meta.soev`. Vision counts as on only when the catalog says `supported` and the backend advertises `images`.

### Direct inference and credentials

No runtime gateway credential or direct image/audio inference surface remains.
Memory embeddings, dictation and speech use soev-api's role primitives with the
requesting person's login token, without a subject assertion or a direct-provider
fallback. The product credential remains `SOEV_API_KEY`.

Speech forwards `input`, optional `voice`, and `response_format` to
`POST /v1/audio/speech`, streaming the response without a shared disk cache.
soev-api selects its speech role and default voice. OWUI exposes no speech
provider URL, key or model settings and no provider model/voice discovery.
The user's voice, playback rate and browser-local speech preferences remain.
Read-aloud/voice-call feature gates and user TTS permissions still apply.

Images are generated and edited only by the agent. OWUI's image routes,
provider settings, admin controls and image playground are removed. The chat
image toggle is available when v2 and the platform's `images` capability are on;
generated images and attachments still render normally. Uploading an audio file
uses ordinary file ingestion; OWUI never transcribes uploads.

Custom-function routes return 403 under v2 (`AGENT_API_ENABLED` and
`SOEV_API_URL`), their UI is hidden, and saved filters, actions, pipes and event
functions do not execute. The gateway pipe template is removed. Custom functions
keep their existing behavior outside v2; this does not enable a direct-provider
fallback for images or audio.

`test/soev/test_no_gateway_keys.py` scans backend source, frontend source and
configuration, the chart and `.env.example` for explicitly listed gateway/key
patterns. Its exceptions preserve the historical config-row Alembic migration
and offline v1 snapshot/restore command. Tests may contain fictional legacy
configuration; upstream locale dictionaries may retain unused labels, not
settings. Existing raw config, user settings, custom-function rows and migration
snapshots are not erased by this source cleanup. The sealed legacy CI fixture
still has fictional provider placeholders; runtime code does not read them.

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
- `SOEV_V2_MODEL_MAP`: JSON of LiteLLM model name to catalog id, e.g. `{"zai-org/GLM-5.3": "glm-5-3"}`. soev-api serves no `/v1/models` with the registry `model_string` yet, so the pairs come from the catalog by hand. An id the map leaves out becomes the catalog default (step 3), so map every model whose chats should keep their own model.
- `SOEV_V2_INGEST_CONCURRENCY` (default 4) and `SOEV_V2_WAIT_SECONDS` (default 0; the chart sets 600).

`--apply` runs these steps in order; each is safe to rerun after a partial failure:

1. **Snapshot.** The config rows the next steps change are kept as stored text, once per migration id. The migration's state (snapshot, model id records, step markers) lives outside Alembic, in schema `owui_v2_migration` on PostgreSQL (`owui_v2_migration_*` tables on SQLite).
2. **Config switch** (once per id). The OpenAI and Ollama connections, `ui.default_models` and the task models are retired; the `SOEV_V2_CONFIG` values are written.
3. **Model ids** (once per id). LiteLLM names become catalog ids in message model ids, chat JSON, assistant base models, base-model override rows and their grants, user pinned models, automations, `ui.default_pinned_models` and `ui.model_order_list`. Each user's default model selection (`settings.ui.models`) is cleared instead, so every user starts on the platform default; `--apply --models` maps it like the rest, since users chose it after the migration. Every other stored id that is neither a catalog id nor an assistant becomes the tenant's default model: the catalog entry marked `default`, which `/api/config` serves as `default_models` (see **Default** above). The run reads it from soev-api's `GET /v1/models` before writing anything, and stops with exit 2 when the catalog marks no default or the map rewrites the default. An unmapped base-model override row is not renamed: its grants move to the default and the row is deactivated, so its name and params never land on the default model. Then every catalog model without a system prompt gets the one of the most recently created base-model row that has one (assistants never count): a model with a row gets it in `params.system`, one without gets a row named after its catalog label. A model whose renamed row brought its own prompt keeps it, and when no base-model row has a prompt nothing is copied. `--apply --models` copies none. Every change, mapped, defaulted or cleared, is recorded with its path, so `--restore` puts it back (a cleared selection only while the user has not chosen a new one). The log prints the default, per site the count of mapped and of defaulted references, per old id how many references went to the default, and which row's system prompt went to which catalog models. `--restore` removes a copied prompt, and a row created for it, only while it still holds that prompt.
4. **Directory and KB copy.** Identity links, groups, collections with grants, folders and cloud schedules.
5. **Re-ingest.** Each file of a local KB without a soev document is submitted through `ingest.submit`. Cloud KBs re-sync from their schedules. The job outcome is written to the file row (processing, then completed or failed), which v1 shares: after a rollback, v1 shows the v2 outcome on re-ingested files, e.g. failed for a file v2 could not ingest, while its own v1 vectors are intact.
   **Chat uploads.** Every upload a stored chat attaches (chat-level and message `files`, type `file`, not an image, not picked from a KB; deleted chats excluded) that is not a KB file is submitted through `ingest.submit` as its owner into their attachments collection, the path a fresh v2 chat upload takes, and its `collection_name` is set to that collection. Files already there, in flight or terminally failed are skipped; an original gone from storage is listed and skipped. Images need nothing: the agent turn reads their bytes from storage. `--apply --uploads` (chart `mode: uploads`) runs only this step, for tenants migrated before it existed.
6. **Memories.** Users whose vectors do not match their memory rows are re-embedded with the app's embedding function.
7. **Reconcile.** Exit 0 when every file is ingested or terminally failed (listed); 75 while ingest jobs still run; 1 on a missing or conflicting collection, missing memory vectors or an unreachable soev-api; 2 on invalid input.
8. **Sign-out** (once per id, after exit 0). Every user's tokens are revoked through the Redis `revoked_at` marker so users sign in again through the configured OIDC provider. `WEBUI_SECRET_KEY` is not rotated. The sign-out is best-effort: if Redis loses its state, a user may keep an OWUI session, but platform operations still require a valid owned OIDC login token. Being signed out is a convenience, not a security control.

`--dry-run` prints the plan without writes (it does not create the state tables either); its one HTTP request is the read of the model catalog for step 3's default. It needs the same `SOEV_V2_*` inputs as `--apply`, and fails like `--apply` when the catalog marks no default. The CLI requires one of `--apply`, `--restore` or `--dry-run`; it no longer runs the directory copy alone. The plan shows the snapshot state, every switched key, the model mapping with counts, the default model with the ids and counts that would go to it, per-KB files to check, memory rows and the sign-out.

`--restore` puts the snapshotted config rows back byte for byte (and removes keys that had no row), and puts back every recorded model id that still holds the value written; references changed since are listed and left. Messages added after the cutover stay. soev-api data and the `owui_v2_migration` state are not touched; the state stays for audit until the cleanup PR drops it. A later `--apply` with the same id switches again from the kept snapshot but does not sign users out again.

Rollback: redeploy the previous full image set and values, and restore the database backup taken before the cutover. v2 migrates the schema past the v1 Alembic head (it drops `agent_config`), so a v1 image does not start on a v2 database.

Not migrated: Confluence KBs (Confluence is out of scope), web-search result collections (transient), Weaviate vectors (re-embedded from the originals), and model ids in feedback records. Person and proven source-identity bindings come from the platform login; OWUI does not mint proven links at sign-in.

## Retired inference surfaces and credential boundaries

The v2 image no longer mounts `/openai/*` or `/api/v1/pipelines/*`, or the main
Anthropic messages and token-count proxies. Admin Connections, personal direct
connections, provider model management and pipeline settings are removed. Saved
`ui.directConnections` are discarded by the user-settings model on reads and
writes; a submitted `model_item` cannot select a provider or bypass catalog access.
Local knowledge embedding and reranking engines, their configuration endpoints,
local query/text-processing routes (`/api/v1/retrieval/query/doc`,
`/query/collection`, `/process/text`), the development `/ef/{text}` probe, and
the Documents embedding/reranking controls are removed. The legacy external-knowledge
connections and query-only sources (direct Qdrant, Milvus and pgvector retrieval),
their test and CRUD routes, and their admin/workspace controls are removed too;
they depended on the same local embedding function. Knowledge is served by the platform. Provider model
management and OpenAI-compatible model unloading are gone; Ollama management
remains. Remote pipeline inlet/outlet calls are removed from chat and task
processing; local custom-function filters are disabled under v2. Single-file and batch ingestion use soev-api; web extraction returns
text without creating local embedding collections. Memory embeddings and dictation
use the soev-api primitives. Memory re-embedding during migration uses the same
platform primitive; connection snapshots and their byte-for-byte restore remain
in `soev/migrate_config.py` and the historical Alembic migration. This removes
runtime use and settings surfaces; it does not erase old values from migration
snapshots or existing raw config/user-settings rows. A later user-settings save
drops the retired direct-connection payload.

The chart defines no model-gateway key, destination, secret, or image-provider
value. The interim image key and External Secrets field are removed too.
`SOEV_API_KEY` authenticates the product to soev-api; it is not an inference
gateway credential. No direct inference credentials remain in runtime settings.
The historical migration and fictional test-fixture exceptions are described
above and enforced by the no-gateway-key assertion.

## Prepared platform thread UI

`ENABLE_V2_THREADS` is a server-only switch, false by default. The authenticated
configuration exposes `features.enable_v2_threads` only when that switch,
`AGENT_API_ENABLED`, and `SOEV_API_URL` are set. This preparation does not mount
the replacement routes or change the current chat and sidebar components.

Activation must mount `soev.thread_routes.create_thread_router` at
`/api/v1/threads`, supplying an async reader of the requesting user's
organisational records from the reconciled chat/thread mapping. Only ids,
thread ids, folders, pins, archive state and last-read timestamps enter that
join. Platform index results determine membership, ordering and cursors; local
records cannot add a thread or supply a title. The client calls `/v1/threads`
through soev-api with the product credential and the requesting user's current
login token in `X-Soev-User-Token`, without a subject assertion or impersonation.

`chat/ThreadWindow.svelte` and `layout/ThreadSidebar.svelte` are the prepared
renderers. Activation supplies the OWUI session credential and a context epoch
that changes on tenant, account, session or authorization-context changes.
The window builds the active `history` from projection pages, preserving message
versions. Fork rows supply version targets; selecting one reads its projection
rather than inventing message bodies. Older pages preserve scroll position.
The authenticated stream adapter supplies projected message replacements through
`projectedMessage(contextEpoch, threadId, generation, message)`. Stale contexts,
other threads and other log generations are ignored. An optional `parent` prop
(`threadId`, `at`) carries the selected index item's fork origin so a reopened
fork can show freshly authorized sibling versions. This unit does not add a raw-event stream
endpoint or connect the existing user-wide socket content relay.

The draft UI contract gives `content` the type `JsonValue`. The adapter accepts
plain text or an OWUI presentation object (`role`, `text`/`content`, `output`,
`statusHistory`, `sources`, files and notices); runtime log envelopes are not
rendered. Structured output and content markup use the existing renderer for
reasoning, tool summaries and citations. The platform projector's presentation
shape must be agreed before activation. Its fallback marker is likewise pending;
the prepared UI recognizes `preferences.fallback: true` for the owner-only notice.

ETags and opaque message versions pass through unchanged. The draft gateway
currently always returns `200` on authorized reads, including conditional reads.
The client also handles an authorized `304` defensively, without retaining a
server-side page. The window always requests fresh projections; persistent
browser caching and authorization-before-cache-display belong to the cache unit.
The initial window is provisionally 20, the draft contract default; the measurement
unit chooses the production size from measured first-paint and paging times.
