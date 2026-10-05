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

## Live document attachments

`soev/live_documents.py` records agent `attached` events as Files whose id is the platform source id.
Their `meta.source` contains the provider reference; bytes are never stored in OWUI. Reference
jobs use the existing durable job poller, skipping upload commit and path moves. File content
routes stream platform originals with the requesting user's assertion. Every v2 turn ensures
and sends the user's chat attachments collection; stored reference metadata is sent next turn.
The platform binds that collection into its live ticket; a missing or changed target
refuses collection_mismatch. Provider identity and Retry-After remain structured client
error fields. Unknown Graph readability is decided from downloaded bytes before staging.

The OneDrive search toggle enables `search_live_documents` and `attach_live_document` together
with `auto` state; both remain off by default. `/cloud-sync/connections/{id}/live-documents`
lists or enables owned OneDrive live grants. The toggle and Connect OneDrive card share the
existing cloud consent popup and verify its origin, opener and connection before polling.
No new OWUI environment variables are required. Platform live documents must be enabled.

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
If an older reference event lacks MIME metadata, successful job polling reads the
completed document as its owning user and fills content_type before marking it ready.

Attach tool summaries show "Could not open document" for refusals and failures.
An attached event (including processing) or an existing document keeps the success label.

OneDrive search uses the cloud-and-magnifier icon in its menu row, pinned button
and tooltip. The picker retains the plain cloud; pinned tooltip labels are
"OneDrive files" and "OneDrive search", translated through i18n.

Consent polling treats suspended:reauth with a cleared last_error as pending until exchange enables the reused connection. A stored failure still terminates polling, and the normal timeout applies.

Composer choices persist in chat.features, including the initial chat creation, and
ui.composerTools user settings for new chats. Model defaults seed only unsaved choices;
loading a chat or changing models never overwrites explicit preferences. Feature gates
control request availability without rewriting saved choices; unavailable selected
features remain disabled in the + menu. Tool/skill/filter selections use the same snapshot.
