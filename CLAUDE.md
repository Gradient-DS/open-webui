# CLAUDE.md

Gradient-DS fork of **Open WebUI** — the soev.ai chat product. SvelteKit 5 + Vite frontend, FastAPI + SQLAlchemy backend, SQLite/PostgreSQL. Supports Ollama and OpenAI-compatible APIs with built-in RAG.

**Fit in the system:** this fork is everything user-facing (chat UI, auth, knowledge bases, admin, GDPR/compliance) plus the proxy seam to the agents-api built in `soev-solutions`; `soev-gitops` deploys one tenant per customer from this repo's chart. Company context, cross-repo architecture, and the decision log live in the sibling `soev-docs` repo (`../soev-docs`, or `$SOEV_DOCS_PATH`, or `~/gradient/soev-docs`) — start at `architecture/repo-map.md`.

Claude scratch (specs, plans, reviews, handoffs) lives in git-ignored `thoughts/<kind>/`; personal commands and agents never go in git. Conventions: `soev-docs/claude-code.md`. Repo decision log: `docs/decisions.md`.

## Commands

### Frontend

```bash
npm install                    # Install dependencies
npm run dev                    # Dev server with hot-reload (port 5173)
npm run build                  # Production build to /build
npm run check                  # TypeScript type checking (svelte-check)
npm run lint:frontend          # ESLint with auto-fix
npm run format                 # Prettier formatting
npm run test:frontend          # Vitest unit tests
```

### Backend

```bash
pip install -e ".[dev]"        # Install with dev dependencies
open-webui dev                 # Dev server with auto-reload (port 8080)
npm run lint:backend           # PyLint checks
npm run format:backend         # Black formatting
```

Best hot-reload: backend (`open-webui dev`, port 8080) and frontend (`npm run dev`, port 5173, proxies to backend) in separate terminals. Full stack via `docker compose up -d`.

### Database migrations (Alembic)

Migrations live in `backend/open_webui/migrations/versions/`. **Gotcha (hit more than once): every Alembic `revision` id must be globally unique across that directory.** When creating a migration by copying an existing one, change BOTH the filename AND the `revision = '...'` line to a fresh id, then `grep -rn "<newid>" backend/open_webui/migrations/versions/` and confirm exactly one match before committing.

Set `down_revision` to the current head. `alembic heads` needs a live DB connection, so with no DB find the head statically: the one `revision` no other file lists as its `down_revision` (watch both `down_revision = '...'` and typed/tuple forms).

## Running against a soev stack

To develop this fork against the soev platform, run it from your checkout (commands above) and point it at a running local stack's soev-api, `http://api.<slug>.soev.localhost`: set `AGENT_API_ENABLED=true` plus the `SOEV_API_*` variables in [docs/agent-api-deployment.md](docs/agent-api-deployment.md). Starting a stack, its naming and where its credentials come from: `soev-docs/architecture/local-development.md`.

## Architecture

- `src/` — SvelteKit frontend: `lib/apis/` (API clients), `lib/components/` (grouped by feature: admin/, chat/, workspace/), `lib/stores/` (global state), `routes/`
- `backend/open_webui/` — FastAPI: `main.py` (app + middleware), `config.py`, `models/` (SQLAlchemy), `routers/` (per feature domain; `retrieval.py` = RAG endpoints), `retrieval/` (loaders, vector adapters incl. Weaviate, web), `socket/` (Socket.IO)
- `cypress/` — E2E tests (base URL `http://localhost:8080`)

## Upstream merge policy

- **Always preserve custom changes.** When resolving upstream merge conflicts, keep our customizations while staying as close as possible to upstream. Never silently drop custom code in favor of upstream.
- Prefer additive changes over modifying upstream code — separate files, conditional mounts, feature flags.
- When upstream refactors a file we've customized, reconcile both: adopt upstream's improvements while keeping our additions.

## Code style

- Frontend: TypeScript strict, Svelte 5 runes (`$state`, `$derived`, `$effect`), Tailwind CSS 4, ESLint + Prettier.
- Backend: type hints on signatures, Black (88 chars), PyLint.
- **i18n: all new user-facing text needs Dutch (nl-NL) translations** — add to both `src/lib/i18n/locales/en-US/translation.json` and `nl-NL/translation.json`. Keys alphabetically sorted; empty string in en-US means "use the key itself". Parse new strings: `npm run i18n:parse`.

## Key files

- `backend/open_webui/main.py` — FastAPI app initialization, middleware stack
- `backend/open_webui/config.py` — all configuration options
- `backend/open_webui/env.py` — environment variable handling
- `src/routes/+layout.svelte` — root layout, app initialization
- `src/lib/stores/` — global state (user, settings, models, chats)

## Models under the v2 runtime

With `AGENT_API_ENABLED` and `SOEV_API_URL` set, `soev/model_catalog.py` makes soev-api
the only source of models (`GET /v1/models`), the default (served as `default_models`) and task completions
(`POST /v1/completions/task`, called from `generate_chat_completion`). Connections, `ui.default_models` and the task
model settings are not used. Details: `docs/agent-api-deployment.md`. Every model goes by its provider/model id:
the catalog's label is not used, and an admin's or a migrated v1 base-model row never renames a catalog model
(`apply_catalog`); assistants keep their own names.

A turn the agent refuses arrives as `invalid_field` with constraint `chat:<reason>`; `utils/agent_v2.py`
(`_REFUSALS`) shows a nl/en message per reason, with the agent's detail only where it names what to fix
(unreadable file names, the missing tool capability), and the generic text when no known reason is given.
The log line carries status, code and constraint, never the detail. Before sending, an unavailable chat item never
blocks the turn: it runs without it and the answer shows a nl/en notice (`chat:message:notices`, stored as
`message.notices`, rendered by `ResponseMessage/TurnNotices.svelte`). A deleted knowledge base, file, note or chat
is named and dropped from the chat's `files` (backend and the frontend's `chatFiles`); one that exists but is not
accessible stays, and knowledge bases are then only counted. Failed and still-processing files are skipped with a
notice too; images keep refusing (`_unimaged`).

A chat's folder reaches the agent too: its prompt joins `user_instructions`, and the middleware puts the items its
owner can still delegate in `metadata['folder_knowledge']` on every routed turn. `agent_v2._folder_items` adds the
folders attached to the chat (`type: 'folder'` entries, read access via `has_folder_access`). Their knowledge bases,
files and notes follow the chat's and the assistant's (deduplicated), read as the chatting user; a folder file is
checked in its collection first so an unreadable one is skipped, not refused. Folder notes fill the room the chat's
ten texts leave. Skipped folder items get "of this folder" notices and are never dropped from the chat.
An assistant's knowledge entries of type note, chat and folder are read the same way, as the chatting user: notes and
chats come after the folder's notes in the shared ten texts (truncated, never refused), folders are expanded like
attached ones, and unreadable ones get the assistant's notices.

## System prompt

Every base model (a catalog model, or a row without `base_model_id`) uses the global system prompt, config
`models.system_prompt` (Admin > Settings > System prompt, shown wherever the models tab is), unless its own row still
has a prompt; `DEFAULT_MODEL_PARAMS.system` comes below it. An empty prompt box (`system: null`) falls back instead of
blanking it. Assistants keep their own prompt only. One resolver, `soev/system_prompt.py`, serves the agent route
(`main.py`) and the direct routers. The v2 migration moves the v1 per-model prompts to the global one: see
`helm/open-webui-tenant/README.md` (`mode: plan`, `soevApi.migrate.systemPrompt`).

## What's new tour

After sign-in, every user sees `layout/Overlay/WhatsNew.svelte` once per minor soev release: the last entry
of `RELEASES` in `utils/whatsNew.ts` (`version` is major.minor, so a patch release never repeats it). Closing
it stores `ui.whatsNewSeen`. Each release lists its slides; a slide shows only when its `SLIDE_GATES` entry
passes for the tenant and user (feature flags, permissions, models in the picker). For a minor release, add
an entry with its slides, new models and release-notes URL; a new slide also needs a gate, a scene in
`WhatsNew/` and its text (nl-NL too). A tenant can name the models to announce per release with
`openWebui.config.whatsNewModels` (`WHATS_NEW_MODELS`, `{"1.3": [catalog ids]}`; an empty list announces
none); without it, the release's own `models` that the user can pick are shown. Both are announced by their id.

## Live document attachments

`utils/agent_v2.py` registers every `attached-document` element of a root `tool_output`
(live, on resume and on replay) through `soev/live_documents.register_attachment`, as a
File whose id is the element's `file_id`, with `attached_by: 'agent'`; the OneDrive
picker registers through the same function with `attached_by: 'user'`. One attach call's
documents are merged into the message files and `chat:message:files` is emitted once.
Their `meta.source` contains the provider reference; bytes are never stored in OWUI. Reference
jobs use the existing durable job poller, skipping upload commit and path moves. File content
routes stream platform originals with the requesting user's assertion. Every v2 turn ensures
and sends the user's chat attachments collection; stored reference metadata is sent next turn.
The platform binds that collection into its live ticket; a missing or changed target
refuses collection_mismatch. Provider identity and Retry-After remain structured client
error fields. Unknown Graph readability is decided from downloaded bytes before staging.

The OneDrive search toggle enables `search_live_documents`, `list_live_folder` and `attach_live_document` together
with `auto` state. `/cloud-sync/connections/{id}/live-documents`
lists or enables owned OneDrive live grants. The toggle and Connect OneDrive card share the
existing cloud consent popup and verify its origin, opener and connection before polling.
Both the OWUI and platform live-document settings must be enabled.

The chat OneDrive picker returns `DocumentReference` metadata (with optional eTag), then
`POST /files/onedrive/attach` calls platform `/v1/attach` with the acting user. Its strict
schema rejects tokens and caller-selected collections. The platform rechecks grant,
readability and size, skipping only reach for the explicit user selection. Missing picker version metadata is resolved by platform inspection; the File stores the inspected reference returned by attach. The picker supports organisational accounts only and always
attaches by reference. Existing file count limits apply. Microsoft picker payload/version availability needs a tenant check.

The consumer API credential needs both ingest and the dedicated attach capability for
picker commissioning; provision attach only to consumer products. The platform budgets
picker attach and inspect per subject and UTC minute (default 10), independently of
idempotency keys. Picker refusals preserve provider and Retry-After, including
connection_required (409), not_readable (422), and provider_throttled (503).
OneDrive search can be pinned to the composer; both controls share the same consent flow.
Set ENABLE_LIVE_DOCUMENTS=true (config key live_documents.enable, default false) alongside
AGENT_API_ENABLED to offer the control. The server gates every requested document tool state
with that setting; enable the matching platform live_documents setting as well.

Reference attachments that are failed or gone are skipped on later turns; processing
ones add a short status note. The composer holds a message (queues it) while any of its
files is uploading or processing, picker references included, until `file:status` settles
it (`utils/messageQueue.ts`). Ordinary upload admission is unchanged.

Under the agent route Continue Response is hidden (`canContinueResponse`): a v2 turn is one new input and cannot
extend an answer. A follow-up sent while an answer streams is queued and sent after it; "send now" (or the queue
turned off) stops the answer and waits for its `chat:tasks:cancel` (`utils/turnSettled.ts`), which comes after the
soev-api cancel is bookmarked, so the next turn continues the thread after the partial answer.
The stop button follows the answer, not the chat task: once every answer of the turn is done (`turnAnswered`),
the task's post-turn work (follow-ups, title, tags, memory) runs on without holding it.

Collection setup
runs only for enabled document tools or reference-bearing turns, caches successful keys
per user, and disables the document tools for that turn if setup fails. Reference File
rows retain supplied content_type for inline original previews and never delete empty
storage paths. Mismatched streamed attachment identities are logged and skipped.
Assistant attachment chips can be dismissed; chat files and message removal markers
persist that choice and prevent historical files or replayed events from restoring it.

Consent cards use provider-specific i18n labels for the configured consent routes.
Connection and grant status is prefetched when InputMenu opens. Connected users reuse
their grant without opening a popup. Suspended:reauth (the broker's persisted lifecycle)
or a reauth error triggers authorization on the next explicit click, preserving the
browser gesture. Provider family support is decided by the platform grant endpoint.

The input menu prefetches connection/grant state and warms business MSAL silently.
A healthy connection never opens a consent popup. Any needed MSAL login starts in
the click stack; a new resource requiring consent asks for another click rather
than opening a delayed popup. Match the picker tenant and object ID against the
connection's provider_tenant_id and provider_identity before choosing its grant.
Picker keys are user-namespaced and identity conflicts return typed 403/409 responses.
If an older reference event lacks MIME metadata, successful job polling reads the
completed document as its owning user and fills content_type before marking it ready.
MSAL uses one organisational client and caches in-flight initialization across picker
preparation and clicks. Personal Microsoft accounts and browser byte downloads are unsupported.

Tool summaries use the agent's declared failed status when ToolOutput.error is set, otherwise done, including partial successes and already-attached documents.
Where no declared status can be filled, `agent_v2._GENERIC` gives each known tool a translated running/done/failed label
(open_document names its document: "Opening document: {{title}}"); only an unknown tool shows its name
("Running {{tool}}…", "Ran {{tool}}", "Could not run {{tool}}"). A document without a title goes by its filename,
and the documents of attached files are known by the id the agent gives them (`<collection_key>/<file_id>`).

OneDrive search uses the cloud-and-magnifier icon in its menu row, pinned button
and tooltip. The picker retains the plain cloud; pinned tooltip labels are
"OneDrive files" and "OneDrive search", translated through i18n.

Consent polling treats suspended:reauth with a cleared last_error as pending until exchange enables the reused connection. A stored failure still terminates polling, and the normal timeout applies.

After an Entra login, `soev/silent_connect.py` (one hook in `utils/oauth.py`) redirects the browser
through a silent soev-api attempt (`silent: true`, `prompt=none`) for the first OneDrive/Outlook
connection that is `pending` or `suspended:reauth`. soev-api returns to `/cloud-sync/connect/done`,
which continues with the next connection or lands on `/auth`. User settings key `soev_silent_connect`
records every tried connection (never tried again) and the one in flight (consumed on return, 300 s), so
popups keep their postMessage page. A failure leaves the manual reconnect; the login never fails on it.

### Data sources

A data source is a tenant-wide platform collection whose key starts with `ds:` (`soev/data_sources.py`). It never
lists as knowledge (`knowledge_store._collections`); `GET /api/v1/data-sources` lists the ones the caller can read,
404 unless `features.enable_data_sources` (`ENABLE_DATA_SOURCES`, config `data_sources.enable`, default false, chart
`openWebui.config.enableDataSources`, plus `AGENT_API_ENABLED` and the soev-api `data_sources` capability). The + menu's
Data sources submenu switches each on or off per chat; each source and the submenu are pinnable. Switched-on ids
persist and travel as `features.data_sources`; `agent_v2._knowledge` appends the readable ones to `input.knowledge`
after the chat's, assistant's and folder's, dropping non-`ds:` ids and everything while the gate is closed. They are
never chat `files`, so no chip and no notice.

### Live mail

`ENABLE_LIVE_MAIL=true` enables the server gate (`live_mail.enable`, default false),
exposed as `features.enable_live_mail` only with `AGENT_API_ENABLED`. The `/api/config`
config read includes the key. `features.live_mail` maps to `search_mail` and `read_mail`;
both stay off unless the gate allows them. The Mail search menu/pin has independent
saved composer state and consents to `outlook_mail` when switched on.

`utils/live-connections.ts` shares consent/prefetch across provider and family pairs.
The proxy uses `/cloud-sync/connections/{id}/live/{family}` for `mail` or
`live_documents`; grants and snapshots stay separate. `consentLabels` includes
Outlook for ActionRequired cards. Mail sources keep Outlook links and render in the
citation panel; they never create File rows or ingestion jobs.

Citation icons use the explicit `source.provider` field, built in
`utils/agent_v2.py` (`_as_source` and `Citations.add`). The live-mail element
types `mail-reference` and `mail-text` identify `outlook_mail`. Document elements
join their `source_id` to File `meta.source.provider`, covering both agent-opened
OneDrive documents and picker attachments, including thread replay. The frontend
reducer preserves this field; `Citations/sourceIcon.ts` maps it to the plain
OneDrive and Outlook logos (`icons/OneDrive.svelte`, `icons/Outlook.svelte`) through `SourceIcon.svelte`. Missing or unknown providers retain the web
favicon/document fallback; names and URLs never identify providers. Previously
saved source payloads without provider metadata are not retroactively classified.
Document-text sources from open_document or attach_live_document carry metadata.granularity=document for whole-document citations; chunk citations remain passage-level.
Web search results (`search-result` elements) are citable for their snippet and carry metadata.granularity=snippet; `Citations/SearchSnippetNote.svelte` shows their title and address, says the page was not opened, and links to it.
Live-document search hits (`live-document` elements) are citable for their search snippet too and carry metadata.granularity=file-snippet, their `web_url` and provider; the same note says the file was not opened and links to it.

Composer choices persist in chat.features, including the initial chat creation, and
ui.composerTools user settings for new chats. Model defaults seed only unsaved choices;
loading a chat or changing models never overwrites explicit preferences. Feature gates
control request availability without rewriting saved choices; unavailable selected
features remain disabled in the + menu. Tool/skill/filter selections use the same snapshot.

Mail shares ui.composerTools/chat.features persistence with every composer feature,
including the initial message. Search statuses retain structured order/from/to/cc
options for English/Dutch rendering, including zero-result searches.

Every composer tool is on or off (`utils/toolState.ts`): the + menu shows a switch, the bar button is blue
when on. On means `auto` for web search, the document writer, OneDrive search and mail search, and every
message for image generation and Code Interpreter; the frontend never sends `required` (Altijd), and saved
Altijd choices read as on. Until a user has saved composer choices, the auto tools start on (web search off
under strict data separation) and the others off. Choices persist with composerPreferences in user settings
and chat.features. Draft restoration restores text and files only; model defaults seed unsaved preferences.

Platform API refusals reach HTTP clients through the `SoevApiError` handler in `main.py`.
`soev/errors.py` selects fixed nl/en messages from `Accept-Language`; problem details are never echoed.

`BYPASS_ADMIN_ACCESS_CONTROL` defaults to false independently of the legacy content-access setting.
KB list/detail `write_access` uses the platform's caller write decision through `AccessGrants`.

Knowledge sharing changes are owner-only. Writers can save content with omitted or unchanged grants;
unchanged grants are stripped before persistence. The local KB access dialog is read-only for other users.
The folder share modal lists the items a folder share leaves out with why (`cloud`, `owner`) from
`GET /folders/{id}/sharing` (owner only), nl/en. That route also says whether the reader may change the folder's
access (`may_share`, owner-only on v2); the modal is read-only otherwise.

`soev/groups.py` narrows platform membership before local writes and publishes widening afterward.
`soev/group_sync.py` serializes these operations, retries widening with backoff, and reconciles at startup
and every `SOEV_GROUP_RECONCILE_MINUTES` (default 15), clearing remote OWUI groups absent locally.
Unknown capabilities refuse narrowing and queue widening. User-deletion callers must propagate group failures.

An assistant's files (explicit `file` entries of a model's `meta.knowledge`) must be files the model owner owns: a
save holding anyone else's is refused (403), and file access, sync and migration ignore such an entry; another's file
reaches an assistant through a knowledge base, whose access is checked live. Deleting a file drops its entries from
the models holding it. They live in its own collection,
`owui-assistant-<model id>` (`soev/assistant_files.py`): owned by the model owner, read and written per the
assistant's access grants, hidden from KB lists. An event sink syncs it on model create/update/access/delete
(access republished, missing files submitted as the owner, removed ones deleted, the collection deleted with the
assistant or its last file; a file still ingesting elsewhere is retried for ten minutes). A turn sends them as
`attachments`, read as the user; one not there yet is skipped as processing and asks for a sync, a deleted one is
noticed as the assistant's. The v2 migration uploads step ingests them into that same collection.

Sharing a folder shares its knowledge (`soev/folder_sharing.py`): everyone a folder (or a same-owner ancestor) is
shared with gets a read grant on the local knowledge bases and notes in it that the folder owner owns, through
`AccessGrants.set_access_grants` asserted as that owner; a folder write grant gives read. Cloud KBs and other
owners' items are never shared. `folder_share_grant` records only the grants this added, so unsharing, removing an
item or moving a folder out revokes those and keeps grants the item already had. The folder routes remove grants
before committing (a failure refuses the change) and add them after. On v2 only the owner changes a folder's grants
or adds items to it; write members may remove items.
A shared folder's files go to its own collection, `owui-folder-<folder id>` (`soev/folder_files.py`): owned by the
folder owner, read by the folder's audience, holding the `file` entries the owner uploaded, deleted when the folder
is unshared, emptied or deleted. Its readers and files are narrowed before a folder change commits and synced in the
background after (missing files submitted as the owner, retried while still ingesting elsewhere). Folder items carry
`folder_id`, and a turn reads a folder file there first, as the user. Hidden from KB lists.

Before snapshot/config reads, the migration waits up to five minutes for this image's Alembic head;
OWUI's init container owns schema upgrades. A timeout exits with a clear retry message.

Knowledge grants project everyone to `platform:pseudo:owui`; writes explicitly include read access.
Access changes commission one `PUT /access` job carrying readers and writers, retaining the owner in both.
KB types come only from the collection's immutable `source_kind`, sent at create. Cloud collections omit the
service reader. Without a content schedule, a writer sees the empty state's "Sync from <provider>" card; only a
provider the tenant cannot sync from (no browser picker adapter, e.g. Confluence, or not enabled) shows a
"not available here" line with Delete for the owner. A new cloud KB opens its picker by itself (the create page's
`start_<provider>_sync` param, consumed once) only when that needs no window: an enabled connection and a provider
`pickSilently` (OneDrive: in-page picker, silent MSAL token); otherwise the card waits for a click.
Skipped files in a synced folder are a collapsed list; each shows the platform's stored `reason` code when the
browser knows it (`SKIP_REASONS` in `utils/sourceState.ts`; unknown codes read as the generic error, free text
never reaches the browser), and writers get Retry failed files (schedule action `retry_failed`, a run that
refetches every file without a landed version) there and in the source's ⋯ menu.

Migration directory reports count the six original grant mappings, dropped cloud grants (listed by KB and
principal), and skipped pending users. Only user/admin roles get links. Cloud KBs migrate with source_kind
and no grants; reconciliation reads them as their owners because the service is not a cloud reader.
Role writes in `models/users.py` serialize with group reconciliation. Pending demotions first revoke
cloud schedules/connections as the still-linked person, then unlink before committing. Approvals queue
failed links for backoff retry. `ensure_link` checks the current local role even for cached refs. Reconcile repairs eligible and pending users and known deleted refs;
the supplied platform contract has no link listing to discover otherwise unknown deleted users.

`owui:group:admins-archive` is synthetic: `group_sync` derives its members from admin roles,
narrows before demotion/deletion, and retries promotions. Reconcile always republishes it.

Admin `DELETE /api/v1/users/{id}` accepts `mode=delete|transfer|archive`, `transfer_to`, and exact
`confirm_shared_deletion` ids (listed by `GET /users/{id}/shared-data`). The existing checkbox sends
`mode=archive`. `services/deletion/offboarding.py` revokes cloud custody as the linked departing person,
narrows groups, disposes resources, unlinks, then commits local erasure/reassignment together.
Cloud resources and memories are always deleted; chats never transfer. Archive snapshots retain a
resource manifest and the configured retention; retries reuse it. Local archive grants are admin-read-only,
and archive deletion/expiry removes the resources before dropping the snapshot. Retention edits publish
the KB expiry before the local update. A failed remote step preserves local content/account rows.

Pending users have already lost cloud custody on demotion. Their offboarding skips subject revocation,
uses service authority for collection inventory/disposition, and keeps group narrowing, unlinking and
local erasure fail-closed. Demotion and offboarding share `soev/cloud_sync.revoke_cloud`.
There is no archive restore endpoint; archived KBs return to a person through the collection ownership
transfer path (`POST /v1/collections/{key}/owner`).
The supplied platform contract has no link listing to discover otherwise unknown deleted users.
The plan's unlink-first order is superseded by spec §10's revoke-before-unlink order.
