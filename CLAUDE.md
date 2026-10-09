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

With `AGENT_API_ENABLED`, `AGENT_API_RUNTIME=v2` and `SOEV_API_URL` set, `soev/model_catalog.py` makes soev-api
the only source of models (`GET /v1/models`), the default (served as `default_models`) and task completions
(`POST /v1/completions/task`, called from `generate_chat_completion`). Connections, `ui.default_models` and the task
model settings are not used. Details: `docs/agent-api-deployment.md`.

A turn the agent refuses arrives as `invalid_field` with constraint `chat:<reason>`; `utils/agent_v2.py`
(`_REFUSALS`) shows a nl/en message per reason, with the agent's detail only where it names what to fix
(unreadable file names, the missing tool capability), and the generic text when no known reason is given.
The log line carries status, code and constraint, never the detail. Before sending, an unavailable chat item never
blocks the turn: it runs without it and the answer shows a nl/en notice (`chat:message:notices`, stored as
`message.notices`, rendered by `ResponseMessage/TurnNotices.svelte`). A deleted knowledge base, file, note or chat
is named and dropped from the chat's `files` (backend and the frontend's `chatFiles`); one that exists but is not
accessible stays, and knowledge bases are then only counted. Failed and still-processing files are skipped with a
notice too; images keep refusing (`_unimaged`).

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
with `auto` state; all remain off by default. `/cloud-sync/connections/{id}/live-documents`
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
OneDrive search can be pinned to the composer; both controls cycle off/auto (off by default)
and share the same consent flow.
Set ENABLE_LIVE_DOCUMENTS=true (config key live_documents.enable, default false) alongside
AGENT_API_ENABLED to offer the control. The server gates every requested document tool state
with that setting; enable the matching platform live_documents setting as well.

Reference attachments that are failed or gone are skipped on later turns; processing
ones add a short status note. Ordinary upload admission is unchanged. Collection setup
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

### Live mail

`ENABLE_LIVE_MAIL=true` enables the server gate (`live_mail.enable`, default false),
exposed as `features.enable_live_mail` only with `AGENT_API_ENABLED`. The `/api/config`
config read includes the key. `features.live_mail` maps to `search_mail` and `read_mail`;
both stay off unless the gate allows them. The Mail search menu/pin has independent
saved composer state, defaults off, and consents to `outlook_mail` on first enable.

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

Composer choices persist in chat.features, including the initial chat creation, and
ui.composerTools user settings for new chats. Model defaults seed only unsaved choices;
loading a chat or changing models never overwrites explicit preferences. Feature gates
control request availability without rewriting saved choices; unavailable selected
features remain disabled in the + menu. Tool/skill/filter selections use the same snapshot.

Mail shares ui.composerTools/chat.features persistence with every composer feature,
including the initial message. Search statuses retain structured order/from/to/cc
options for English/Dutch rendering, including zero-result searches.

PDF writer auto/required/off choices persist with composerPreferences in user settings and chat.features. Draft restoration restores text and files only; model defaults seed unsaved preferences.
