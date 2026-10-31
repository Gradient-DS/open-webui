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
| `SOEV_API_SERVICE_PRINCIPAL` | `soevApi.servicePrincipal` | Declared Open WebUI service principal |

`SOEV_ARCHIVE_READERS_GROUP_REF` is required whenever `SOEV_API_URL` is set.
Set it to the seeded `platform:group:<id>` for **Archive readers** through the
chart's existing extra environment variables. There is no default. Local admin
roles do not grant archive readership.

Groups and flattened person memberships are pulled from paged `GET /v1/groups`
at startup and at most every third of the snapshot lifetime (5/3 seconds), measured
from the start of each pull. One worker holds a database lease of 5/3 seconds,
renewed by heartbeats while fetching; the others poll the lease and schedule row.
`SOEV_GROUP_RECONCILE_MINUTES` (default 15) may shorten that interval. The product
credential needs directory read. A failed or invalid page preserves the previous
complete snapshot without renewing its validity; retries use the same bound. The console manages
group names and membership. OWUI admins can edit local feature permissions and
sharing settings, keyed by platform group id; these never grant platform actions.
OAuth, LDAP, SCIM, trusted-header and signup defaults cannot manage memberships.
Disable OAuth/LDAP group management before reopening; attempts return
`403 platform_groups_read_only`.

The cutover Job can call `soev.migrate_groups.cutover_groups(session, group_map)`
with 9g's OWUI-id to platform-group-ref mapping before starting the mirror. It
rewrites groups, memberships and group access grants in the caller's transaction,
preserves product settings, returns changed-row counts, and refuses unmapped ids
or conflicting targets. The caller commits or rolls back; the function makes no
network calls.

The chart's routing switch is `openWebui.config.agentApiEnabled: "true"`.
With External Secrets enabled and a platform URL set, provide `soevApiKey` in the configured 1Password item instead of inline secrets.
Startup validates the product credential when the platform URL is set. Missing platform
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

### Remaining legacy subject callers

None. OWUI has no subject signer, subject-header client, identity-link publishing
or impersonation context. User calls require the requesting person's login token;
service calls explicitly select `SoevClient.as_service()`.

The durable job poller uses product authority. A completed reference that lacks
MIME metadata stays processing until the owner's next authenticated request, when
the document is read using that request's login token. References with supplied
MIME metadata can finish in the poller. Failed lookups retain the durable job for
a later owner request. Offline cutover work belongs to the platform migrator; see
[cutover-handoff.md](cutover-handoff.md).

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
- **Tasks.** Titles, tags, follow-ups, emoji, queries, autocomplete and MoA send typed `soev.task/1` requests to `POST /v1/completions/task` as the acting user. soev-api routes each invocation to the project's task agent, which owns the prompts and model selection. Thread-derived tasks send the chat id as `source_thread`; text tasks send raw input. Invocation keys stay stable for the same chat, task and message, or the same draft input. The platform returns one bounded JSON result; OWUI maps it to the frontend's completion shape (SSE for MoA). `TASK_MODEL`, `TASK_MODEL_EXTERNAL`, task sampling and prompt-template settings are ignored on this path. The admin template fields remain visible because the settings page has no platform-mode switch. Image-prompt tasks are unavailable in platform mode; context compaction, memory review and local tool selection have no platform task kind.
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
patterns. Its exceptions preserve the historical config-row Alembic migration. Tests may contain fictional legacy
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

## Cutover

The legacy v1-to-v2 migration CLI and chart Job are removed. Offline migration
runs in the platform cutover tooling; see [cutover-handoff.md](cutover-handoff.md).

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
use the soev-api primitives. Cutover copies existing memory vectors after verifying the embedding model.
The historical Alembic config migration remains. This removes
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

New chats reserve an id via `POST /v1/chat/threads:reserve` without running input; the followed first turn streams `POST /v1/chat/threads` with that `thread_id`, and later turns use `/inputs`.

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

The directory mirror requires `SOEV_GROUP_SNAPSHOT_MAX_AGE_SECONDS=5` when
`SOEV_API_URL` is set, matching the platform grant-context revocation bound.
Group authorization checks the database clock in the SQL that reads memberships:
`clock_timestamp() < fetch_started_at + bound`. The claim records each attempt's
start in database time before fetching, and successful replacement publishes that
start atomically with the memberships. An absent or expired snapshot grants no
membership; startup needs no worker baseline. Worker clocks and delayed reads
cannot extend validity. Failed or slow pulls do not renew evidence. Unchanged
snapshots refresh evidence without rewriting memberships. The refresh interval is
capped at one third of the bound, including retries; `SOEV_GROUP_RECONCILE_MINUTES`
may shorten it. Database waits and the complete network fetch have timeouts within
the remaining validity, and a fencing token prevents superseded pullers from
committing. Note and collaborative document events recheck each recipient on its
delivery worker and evict readers who lost access, including when a group snapshot
expires.

The application database role can forge mirror evidence. This is an accepted
residual risk: the same role can already write access grants directly. A separate
publisher role is not part of this design.
