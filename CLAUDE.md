# CLAUDE.md

Gradient-DS fork of **Open WebUI** — the soev.ai chat product. SvelteKit 5 + Vite frontend, FastAPI + SQLAlchemy backend, SQLite/PostgreSQL. Supports Ollama and OpenAI-compatible APIs with built-in RAG.

**Fit in the system:** this fork is everything user-facing (chat UI, auth, knowledge bases, admin, GDPR/compliance) plus the proxy seam to the agents-api built in `soev-solutions`; `soev-gitops` deploys one tenant per customer from this repo's chart. Company context, cross-repo architecture, and the decision log live in the sibling `soev-docs` repo (`../soev-docs`, or `$SOEV_DOCS_PATH`, or `~/gradient/soev-docs`) — start at `architecture/repo-map.md`.

Personal Claude workflow files (commands, memory systems, scratch notes) never go in git — `thoughts/`, `plans/`, `collab/`, and `.superpowers/` are git-ignored. See `soev-docs/claude-code.md`.

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
