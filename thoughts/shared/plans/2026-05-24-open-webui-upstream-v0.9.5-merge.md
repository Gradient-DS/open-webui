# Open WebUI Upstream v0.8.12 → v0.9.5 Merge — Implementation Plan

**Date:** 2026-05-24
**Author:** @lexlubbers + claude (Opus 4.7 1M)
**Status:** Ready to execute
**Branch:** `feat/upstream-v0.9.5-merge` (off `dev`)
**Research:** [`thoughts/shared/research/2026-05-24-open-webui-upstream-v0.9.5-merge-inventory.md`](../research/2026-05-24-open-webui-upstream-v0.9.5-merge-inventory.md)

---

## Overview

Bring our fork from upstream v0.8.12 to upstream v0.9.5 using a **true `git merge`** with hunk-by-hunk conflict resolution. Three named files (`routers/retrieval.py`, `MessageInput.svelte`, `InputMenu.svelte`) are carved out — kept at our version (`--ours`) with a Phase 4 audit step to manually port any security/bugfix hunks upstream made inside them. Calendar + Automations are adopted as part of this merge (separate Phase D ticket consolidated here). Reported version bumps to `0.9.5` after merge lands.

## Current State Analysis

- **Merge gap:** 557 upstream commits, 491 of ours, 892 files differ, +51,650/−154,532 lines.
- **Async refactor** (v0.9.0+) and **psycopg-v3 driver swap** (v0.9.2 breaking) are concentrated in files we never touched (`internal/db.py`, new utils). High-value, low-conflict.
- **Security audit** in upstream: ~25 commits + ~60 changelog items across v0.9.0/v0.9.2/v0.9.5.
- **6 Tier-0 files** are heavily customized on both sides (`main.py`, `config.py`, `middleware.py`, `routers/{auths,knowledge,retrieval,configs}.py`, `MessageInput*.svelte`, `routes/auth/+page.svelte`). Prior full-merge attempts failed because of the "take + replay" strategy losing work. This plan uses **true 3-way merge** instead.
- **Alembic chain collision detected:** our `d4e5f6a7b8c9_add_soft_delete_columns.py` shares its revision ID with upstream's new `d4e5f6a7b8c9_add_automation_tables.py`. Must rename upstream's copy.
- **Scheduler coexistence verified:** upstream's `utils/automations.py::scheduler_worker_loop` polls `Automation`/`CalendarEvent` tables independently of our `services/sync/scheduler.py` (polls `Knowledges`). No env var clash, no DB contention. **Zero changes needed to our scheduler.**
- **AIOHTTP_CLIENT_ALLOW_REDIRECTS risk to cloud sync = zero:** OneDrive/GDrive/Confluence sync use `httpx`, not `aiohttp`. The new env var only affects 26 upstream-owned `aiohttp` call sites (LLM proxies, generic OAuth, tools/web fetch, webhook).

### Key Discoveries

- True merge is viable for Tier-0 files where both sides are *additive* (e.g., `main.py`, `config.py`, `auths.py`) — conflict markers fire on overlapping imports + a few same-area blocks, manageable with the merge tool. (Research §4.)
- Migration merge follows an existing fork pattern: `e5f6a7b8c9d0_merge_upstream_v089.py` was a multi-parent no-op merge node — repeat for v0.9.5.
- `pyproject.toml` switches `asyncpg` → `psycopg[binary]` v3 (`backend/open_webui/internal/db.py` after upstream pull will use the new driver).
- `socket/main.py:185/−62` upstream rewrite includes role-invalidation on socket sessions — useful security feature, conflicts with our 58 lines.
- Frontend pickers use browser `fetch()` (`src/lib/utils/onedrive-file-picker.ts`, `google-drive-picker.ts`) — also out of scope for the aiohttp env var.

## Desired End State

After this plan:

- `dev` contains a merge commit `Merge upstream v0.9.5` linking our 491 commits with upstream's 557.
- `package.json` reports `0.9.5`. Internal fork release tag becomes `v1.2.0`.
- All custom features still work end-to-end (TOTP, OneDrive/GDrive/Confluence sync, agent proxy, data export, retention, archival, email invites, typed KBs, feature flags, acceptance modal).
- Calendar workspace + Scheduled Automations are available behind their respective `ENABLE_*` env vars.
- All v0.9.0/0.9.2/0.9.5 security fixes that touch *files we control* are applied. Security fixes inside the 3 carved-out files are ported by hand in Phase 4 and documented.
- `alembic upgrade head` runs cleanly on fresh SQLite and on a copy of a tenant Postgres DB.
- All upstream-introduced env vars are reviewed; defaults that break our flows are overridden in a Gradient defaults layer in `config.py` and documented in `.env.example`.

**Verification:** see Success Criteria per phase below.

## What We're NOT Doing

- Adopt the Tauri Desktop App (separate binary, irrelevant to our deployment).
- Refactor our sync scheduler to use upstream's atomic-claim DB pattern (follow-up ticket — current in-memory claim is acceptable for 60-min intervals).
- Adopt Channels-with-tools streaming (out of scope for our customer profile; revisit if requested).
- Adopt the new `shared_chats` table for our existing share pattern (we keep our current `share_id` pattern; the new table coexists but we don't migrate data).
- Coordinate `feat/vink` / `feat/email-2fa` / other in-flight branches — they rebase onto the new `dev` when their owners are ready.
- Auto-flip any of the 3 carve-out files to upstream's version — `--ours` always, audit by hand.

## Implementation Approach

A true `git merge upstream/main --no-ff --no-commit` produces conflict markers across ~30–40 files. We resolve them in **dependency order** (build deps → DB layer → middleware → routers → UI), then run Alembic + tests, then bump version. The 3 carve-out files get `git checkout --ours` immediately after the merge starts to short-circuit useless conflict work; their upstream diffs are saved aside for Phase 4 hand-port.

All env vars upstream added (~15–20 new ones) are enumerated in Phase 0 and decided one-by-one: adopt upstream default, or override in our Gradient defaults layer (a section in `config.py` plus `.env.example` documentation).

---

## Phase 0 — Pre-merge prep & spike

### Overview
Branch, snapshot, resolve known collisions in advance, decide on default-postures for new env vars, smoke-test the few aiohttp call sites that might break with redirect-blocking.

### Changes Required:

#### 1. Create branch & worktree

```bash
cd /Users/lexlubbers/Code/soev/open-webui
git fetch upstream
git checkout dev
git pull origin dev
git checkout -b feat/upstream-v0.9.5-merge
git tag pre-v0.9.5-merge-baseline
```

Optionally use a worktree so `dev` stays usable in parallel:

```bash
git worktree add ../.worktrees/upstream-v0.9.5-merge feat/upstream-v0.9.5-merge
```

#### 2. Snapshot a tenant DB schema for migration testing

```bash
pg_dump --schema-only "$TENANT_DATABASE_URL" > /tmp/tenant-schema-pre-v0.9.5.sql
```

(Run against a non-prod or read-only replica.) Record the current Alembic head from prod:

```bash
psql "$TENANT_DATABASE_URL" -c "SELECT version_num FROM alembic_version;"
```

Expected: `f9a0b1c2d3e4` (our current head).

#### 3. Enumerate new upstream env vars & decide default-posture

Run:

```bash
git diff 9bd84258d09eefe7bf975878fb0e31a5dadfe0f8..upstream/main -- backend/open_webui/config.py backend/open_webui/env.py | grep -E "^\+.*os\.environ\.get\(" | head -80
```

For each new env var, fill in this table (commit it to `thoughts/shared/research/v0.9.5-env-defaults.md`):

| Env var | Upstream default | Behavior | Our decision | Override in `config.py`? |
|---|---|---|---|---|
| `AIOHTTP_CLIENT_ALLOW_REDIRECTS` | `false` | Block 3xx on aiohttp calls | Adopt (httpx-based sync unaffected; verify in spike below) | No |
| `IFRAME_CSP` | restrictive default | CSP on srcdoc iframes (artifacts, tool embeds) | Inspect — possibly override for generative-UI artifacts | TBD by spike |
| `PROFILE_IMAGE_ALLOWED_MIME_TYPES` | `image/png,image/jpeg,image/gif,image/webp` | Strict MIME on data-URI profile images | Adopt | No |
| `SCHEDULER_POLL_INTERVAL` | `10` (seconds) | Calendar/Automation poll | Adopt | No |
| `CALENDAR_ALERT_LOOKAHEAD_MINUTES` | `10` | Calendar reminder lookahead | Adopt | No |
| `AUTOMATION_MAX_COUNT` | (admin-set) | Per-user automation limit | Adopt; we may add a tenant default in Helm | No |
| `AUTOMATION_MIN_INTERVAL` | (admin-set) | Minimum schedule interval | Adopt | No |
| `AUDIO_STT_SKIP_PREPROCESSING` | `false` | Send STT audio directly | Adopt | No |
| `TERMINAL_PROXY_HEADERS` | unset | Custom response headers for terminal proxy | Adopt | No |
| `AIOHTTP_*` pool tuning vars | upstream defaults | Outbound HTTP connection behavior | Adopt | No |
| (signout-method change) | `POST` only | Was `GET`+`POST`; security hardening | Adopt; update our frontend signout to `POST` if any uses `GET` | n/a |
| `REDIS_SOCKET_CONNECT_TIMEOUT` | (existing) | Honored consistently in v0.9.0 | Adopt | No |
| `ENABLE_IMAGE_CONTENT_TYPE_EXTENSION_FALLBACK` | `false` | Stricter image MIME | Adopt | No |
| `OPEN_WEBUI_SECURITY_HEADERS` | upstream defaults | Browser security headers | Adopt | No |
| `RAG_RERANKING_BATCH_SIZE` | upstream default | Reranker batch size | Adopt | No |

**Gradient defaults layer pattern:** when we choose to override an upstream default, do it in `backend/open_webui/config.py` immediately after the upstream import block, wrapped in a clearly-labeled section:

```python
# === Gradient defaults (override upstream defaults) ===
# Override IFRAME_CSP because our generative-UI artifacts need looser policy.
# Upstream default: <quote upstream value>
# Our default: <our value>
IFRAME_CSP = os.environ.get("IFRAME_CSP", "<gradient-permissive-default>")
# === End Gradient defaults ===
```

Document every override in `.env.example` with a `# Gradient default differs from upstream` comment.

#### 4. Spike: AIOHTTP redirect-blocking smoke test

The cloud-sync surface is `httpx`-based, so unaffected. The 26 aiohttp call sites that could be affected (`routers/{openai,ollama,agent_proxy,pipelines,audio,functions,tools,discovery,auths,configs,terminals}.py`, `utils/{oauth,tools,agent,anthropic,feedback_report,code_interpreter,misc,webhook}.py`, `utils/images/comfyui.py`, `utils/telemetry/instrumentors.py`, `retrieval/utils.py`, `retrieval/web/utils.py`, `retrieval/loaders/mistral.py`, `main.py`) are mostly POST-to-LLM-API (don't expect 3xx) or already have explicit `allow_redirects=False` (OAuth, code interpreter, web utils — all `utils/oauth.py:635`, `utils/tools.py:1283/1308`, `utils/code_interpreter.py:91`, `retrieval/web/utils.py:569`).

Verify before merge by grepping for `allow_redirects=True` and any 302-expecting flows:

```bash
grep -rn "allow_redirects" backend/open_webui/ | grep -v "False"
```

Expected: zero matches in our backend (upstream commits already removed the `=True` ones in v0.9.5 security batch). If any are found, they're the failure points — decide per-call whether to keep `allow_redirects=True` explicitly or let the new env var control.

#### 5. Resolve Alembic ID collision in advance

Our migration `backend/open_webui/migrations/versions/d4e5f6a7b8c9_add_soft_delete_columns.py` uses `revision = "d4e5f6a7b8c9"`. Upstream's new `d4e5f6a7b8c9_add_automation_tables.py` will conflict. **Do not rename ours** (tenants have applied it). Plan to rename upstream's copy during Phase 2 — record the rename now so it isn't forgotten:

Rename to: `d5e6f7a8b9ca_add_automation_tables.py`, `revision = "d5e6f7a8b9ca"`, leave `down_revision` and content alone.

Add a header note in the renamed file:

```python
"""Add automation tables.

Original upstream revision id: d4e5f6a7b8c9
Renamed in Gradient-DS fork to d5e6f7a8b9ca due to collision with our
add_soft_delete_columns migration. See plan:
thoughts/shared/plans/2026-05-24-open-webui-upstream-v0.9.5-merge.md
"""
```

### Success Criteria:

#### Automated Verification:
- [x] Branch `feat/upstream-v0.9.5-merge` exists and is up to date with `dev`: `git rev-parse feat/upstream-v0.9.5-merge` → `eef4a4f14`
- [x] Tag `pre-v0.9.5-merge-baseline` exists on dev tip
- [x] Tenant DB schema snapshot saved at `.local/schema-snapshots/tenant-schema-pre-v0.9.5.sql` (gitignored; reusable — see `.local/schema-snapshots/README.md`). 1,728 lines, 44 tables, alembic head `f9a0b1c2d3e4` (matches plan)
- [x] Env-defaults research file at `thoughts/shared/research/v0.9.5-env-defaults.md` (234 lines)
- [x] No unaccounted `allow_redirects=True` in our backend — zero matches. Spike write-up: `thoughts/shared/research/v0.9.5-aiohttp-redirect-spike.md`

#### Manual Verification:
- [ ] Env-defaults table reviewed by Lex — every row has a decision filled in. (Five §E open questions await Lex's call.)
- [x] Worktree mounted and accessible at `.worktrees/upstream-v0.9.5-merge`.

#### Plan corrections discovered in Phase 0:
Two issues with Phase 2 spotted while documenting the Alembic collision — write-up at `thoughts/shared/research/v0.9.5-migration-chain.md`:
1. The rename of upstream's `d4e5f6a7b8c9_add_automation_tables` to `d5e6f7a8b9ca` also requires updating the **child** migration's `down_revision` in `b7c8d9e0f1a2_add_last_read_at_to_chat.py` (currently `'d4e5f6a7b8c9'` → must become `'d5e6f7a8b9ca'`). The plan's Phase 0 step 5 only renames the file itself.
2. The plan's Phase 2 step 3 has the wrong merge-node parent — it lists `("f9a0b1c2d3e4", "d5e6f7a8b9ca")` but the correct upstream **top** is `a0b1c2d3e4f5` (verified by walking each migration's `down_revision`). Should be `("f9a0b1c2d3e4", "a0b1c2d3e4f5")`. Also the chain order in Phase 2 step 2 is reversed (actual order documented in the migration-chain research file).

**Implementation Note:** After Phase 0 complete, pause for Lex's review of the env-defaults table before kicking off the merge.

---

## Phase 1 — Execute the true merge

### Overview
Run `git merge upstream/main --no-ff --no-commit`. Pre-empt the 3 carve-outs. Resolve conflicts in dependency order. End with a single merge commit (not pushed yet).

### Changes Required:

#### 1. Start the merge

```bash
cd /Users/lexlubbers/Code/soev/open-webui  # or your worktree
git merge upstream/main --no-ff --no-commit
```

Expect ~30–40 files with conflict markers. List them:

```bash
git diff --name-only --diff-filter=U > /tmp/conflicts.txt
wc -l /tmp/conflicts.txt
```

#### 2. Save upstream diffs for the 3 carve-outs (audit material for Phase 4)

```bash
mkdir -p thoughts/shared/research/v0.9.5-carveout-diffs
for f in backend/open_webui/routers/retrieval.py src/lib/components/chat/MessageInput.svelte src/lib/components/chat/MessageInput/InputMenu.svelte; do
  out="thoughts/shared/research/v0.9.5-carveout-diffs/$(echo $f | tr / -).diff"
  git diff 9bd84258d09eefe7bf975878fb0e31a5dadfe0f8..upstream/main -- "$f" > "$out"
done
ls thoughts/shared/research/v0.9.5-carveout-diffs/
```

#### 3. Pre-empt the 3 carve-outs

```bash
git checkout --ours backend/open_webui/routers/retrieval.py
git checkout --ours src/lib/components/chat/MessageInput.svelte
git checkout --ours src/lib/components/chat/MessageInput/InputMenu.svelte
git add backend/open_webui/routers/retrieval.py src/lib/components/chat/MessageInput.svelte src/lib/components/chat/MessageInput/InputMenu.svelte
```

These three are now done. Audit happens in Phase 4.

#### 4. Resolve in dependency order

For each file group below: open the merge tool (`code -m`, `git mergetool`, or VS Code's "Resolve in Merge Editor"), accept both sides where additive, manually rewrite where logic overlaps. **Never `--theirs`.** **Never blanket `--ours`** for the non-carve-out files. After resolving a group, `git add` and move on.

##### 4a. Build & dependency files (must be first to make the project buildable)

**Files:** `pyproject.toml`, `package.json`, `Dockerfile`, `Makefile`, `.env.example`

- `pyproject.toml`: accept upstream's psycopg[binary] / aiosqlite / new deps. Keep our extras (`pyotp`, `qrcode`, `msgraph-sdk` or whichever Microsoft lib, `google-api-python-client`, etc.). Resolve dependency version conflicts to highest spec.
- `package.json`: take upstream's deps + add our deps back. Leave `version` at `"0.8.12"` for now — bumped in Phase 3.
- `.env.example`: take upstream wholesale, append our Gradient-only env vars at the bottom in a `# === Gradient extensions ===` section. Add `# Gradient default differs from upstream` comments to any overridden defaults per Phase 0 table.
- `Dockerfile`, `Makefile`: take upstream where it doesn't break our build steps; rebase our custom steps on top.

##### 4b. Core async DB plumbing (mostly zero-conflict)

**Files:**
- `backend/open_webui/internal/db.py` — accept upstream wholesale (we have 0 changes here).
- New upstream files (no conflict, just `git add`): `backend/open_webui/utils/asgi_middleware.py`, `utils/session_pool.py`, `utils/security_headers.py`, `utils/headers.py`, `utils/response.py`, `retrieval/vector/async_client.py`, `__init__.py`, `constants.py`.

##### 4c. Calendar + Automations new modules (zero conflict)

**Files (all new from upstream, no conflict):**
- `backend/open_webui/models/automations.py`, `models/calendar.py`, `models/shared_chats.py`
- `backend/open_webui/routers/automations.py`, `routers/calendar.py`
- `backend/open_webui/utils/automations.py`, `utils/calendar.py`
- `backend/open_webui/migrations/versions/d4e5f6a7b8c9_add_automation_tables.py` → **rename to** `d5e6f7a8b9ca_add_automation_tables.py` with revision update per Phase 0 step 5. (Other new migrations don't collide — leave them as-is.)
- Frontend: `src/routes/(app)/automations/+page.svelte`, `src/routes/(app)/calendar/+page.svelte`, `src/lib/components/automations/*`, `src/lib/components/calendar/*`, `src/lib/apis/automations/*`, `src/lib/apis/calendar/*`.

##### 4d. Other new retrieval/web modules (zero conflict)

**Files:** `backend/open_webui/retrieval/loaders/paddleocr_vl.py`, `retrieval/web/brave_llm_context.py`, `retrieval/web/yandex.py`. Just `git add`.

##### 4e. `env.py` + `config.py` (additive both sides)

Both sides added env-var declarations. Conflict markers will appear at import block + at any same-region additions. Resolution: accept both sides' additions. Apply the Gradient defaults overrides from Phase 0 step 3 in the same edit. Keep section comments for navigability.

Specific spots to watch:
- `config.py` `PersistentConfig` extensions — ours must survive
- `config.py` scheduler/automation/calendar env-var section — pure upstream, add as block
- `env.py` `AIOHTTP_*`, `IFRAME_CSP`, `SCHEDULER_*`, `AUTOMATION_*`, `CALENDAR_*` — pure upstream, add as block

##### 4f. `main.py` (additive both sides — the largest file conflict)

Both sides added imports, router mounts, lifespan handlers. Expected conflict blocks: ~5–10.

Resolution playbook:
1. Resolve the import-block conflict by keeping all imports (ours + upstream's), sorted by stdlib → third-party → local.
2. Lifespan: accept upstream's new lifespan structure (async DB engine init, ASGI middleware, scheduler start). Add our calls to start sync schedulers (`start_onedrive_scheduler`, `start_google_drive_scheduler`, `start_confluence_scheduler`) immediately after upstream's `asyncio.create_task(scheduler_worker_loop(app))` call.
3. Router mounts: accept upstream's new mounts (`/api/v1/automations`, `/api/v1/calendar`) and keep all ours (`/api/v1/agent`, `/api/v1/integrations`, `/api/v1/sync/*`, `/api/v1/totp`, `/api/v1/export`, `/api/v1/invites`).
4. Middleware: accept upstream's new ASGI middleware additions; keep our agent/auth middleware where it sits.
5. Static-asset mounts and any redirect endpoints: keep as-is.

##### 4g. `utils/middleware.py` (upstream rewrite, our 163 additions)

Upstream did a structural ASGI rewrite for async. Our 163 lines are agent/auth/log handling.

Resolution playbook:
1. Take upstream's new ASGI middleware skeleton.
2. Re-insert our `process_chat_payload`, `process_chat_response`, agent-routing hooks, and PII-stripping log filters in the same logical positions they sit today.
3. Verify upstream's new `CommitSessionMiddleware` (with health-probe bypass from `b63da90ae`) is mounted correctly.

##### 4h. `socket/main.py`

Upstream rewrote for async + role-invalidation. Resolve by accepting upstream's task-management changes + role-invalidation; re-inserting our 58 lines of agent/sync event handlers.

##### 4i. `routers/auths.py` (both sides touched login flow)

Conflict spots:
- OAuth handler — upstream added 2.1 PKCE enforcement, allowed-domains check on token exchange, JWT expiry alignment, first-user race protection, backchannel logout. Keep all those + our TOTP partial-JWT flow.
- LDAP — upstream rejects empty passwords. Keep + our LDAP signup webhook parity.
- Signup webhook — both sides touched. Merge carefully.

##### 4j. `routers/knowledge.py`, `routers/configs.py`, `routers/files.py`

- `routers/knowledge.py`: upstream added per-file read access check on folder/KB attach, knowledge collection query access enforcement, embedding deadlock fix. Keep + our Typed KB, suspension lifecycle, archival hooks, Confluence integration.
- `routers/configs.py`: we own 600+ lines; upstream had light updates. Accept upstream hunks where they don't touch our Confluence/OneDrive/GDrive runtime-editable config endpoints.
- `routers/files.py`: upstream did path-safety, async storage I/O, file ownership checks. Keep + our `DOCUMENT_PROCESSING_TIMEOUT`, cloud-sync filetype detection.

##### 4k. Model files (`backend/open_webui/models/*.py`)

For each: take upstream's structural changes, replay our added fields/methods as targeted hunks:
- `models/auths.py` — preserve TOTP columns (`totp_secret`, `totp_enabled`, `totp_last_used_at`, `twofa_grace_started_at`)
- `models/users.py` — preserve `archived_at`, `scim` JSON
- `models/knowledge.py` — preserve `type` column, suspension fields, soft-delete `deleted_at`
- `models/files.py` — preserve any DOCUMENT_PROCESSING_TIMEOUT-related fields
- `models/chats.py` — preserve any added fields
- `models/messages.py`, `models/channels.py`, `models/models.py`, `models/groups.py`, `models/prompts.py`, `models/chat_messages.py` — accept upstream, replay anything ours

##### 4l. Frontend layout & auth route

- `src/routes/+layout.svelte` — upstream rewrote (+157/−54), we have 10 lines. Take upstream, re-insert our 10 lines (likely feature-flag initialization).
- `src/routes/auth/+page.svelte` — upstream didn't touch this in v0.9.x window. Our changes should land conflict-free.

##### 4m. Other frontend files (heavy upstream churn, light from us)

`src/lib/components/chat/Chat.svelte`, `chat/ModelSelector/Selector.svelte`, `chat/Messages/Message.svelte`, `channel/Messages/Message.svelte`, `layout/Sidebar.svelte`, `layout/Sidebar/UserMenu.svelte`, `admin/Evaluations/Feedbacks.svelte`, `chat/ContentRenderer/FloatingButtons.svelte` — apply true 3-way merge per file.

##### 4n. Translations

`src/lib/i18n/locales/*/translation.json`: keys are alphabetically sorted. Conflicts are JSON-level. Resolution: keep both sides' keys (merge sets), re-sort alphabetically, validate JSON:

```bash
for f in src/lib/i18n/locales/*/translation.json; do python -c "import json,sys; json.load(open('$f'))" || echo "BROKEN: $f"; done
```

Pay special attention to `nl-NL/translation.json` (we have many custom-feature keys there). All our keys must survive.

##### 4o. Lockfiles

Don't manual-merge `package-lock.json` or `uv.lock`. After all other conflicts resolved:

```bash
git checkout --theirs package-lock.json uv.lock 2>/dev/null || true
rm -f package-lock.json uv.lock
npm install
uv sync  # or `pip install -e ".[dev]"` then `pip freeze > requirements.lock` per repo convention
git add package-lock.json uv.lock
```

#### 5. Commit the merge

After every conflict is resolved (`git status` shows zero `UU`):

```bash
git status
git diff --check  # check for accidental conflict markers left in files
git commit  # opens editor with default merge message; expand it to summarize phases
```

Suggested merge commit message:

```
Merge upstream open-webui v0.9.5 into dev

Brings the fork from v0.8.12 to v0.9.5. Adopts:
- Async DB refactor (v0.9.0) + psycopg-v3 driver swap (v0.9.2)
- All ~25 security commits across v0.9.0/0.9.2/0.9.5
- Calendar workspace + Scheduled Automations (new env-gated features)
- PaddleOCR-vl loader, Brave LLM Context search, security headers util

Carved-out files (kept at our version, see Phase 4 audit):
- backend/open_webui/routers/retrieval.py
- src/lib/components/chat/MessageInput.svelte
- src/lib/components/chat/MessageInput/InputMenu.svelte

Migration: renamed upstream's d4e5f6a7b8c9_add_automation_tables.py to
d5e6f7a8b9ca due to revision-id collision with our soft-delete migration.

Plan: thoughts/shared/plans/2026-05-24-open-webui-upstream-v0.9.5-merge.md
Research: thoughts/shared/research/2026-05-24-open-webui-upstream-v0.9.5-merge-inventory.md
```

### Success Criteria:

#### Automated Verification:
- [ ] No remaining conflict markers in any file: `! git grep -nE '^(<<<<<<<|=======|>>>>>>>) ' -- ':!thoughts/shared/research/v0.9.5-carveout-diffs/'`
- [ ] No `UU` status entries: `git status --short | grep -E '^UU' | wc -l` returns `0`
- [ ] JSON translations parse cleanly: `for f in src/lib/i18n/locales/*/translation.json; do python -c "import json; json.load(open('$f'))"; done`
- [ ] `pyproject.toml` parses: `python -c "import tomllib; tomllib.loads(open('pyproject.toml').read())"`
- [ ] Backend imports cleanly: `python -c "import open_webui.main"`
- [ ] Frontend type-checks: `npm run check`
- [ ] Frontend lint passes: `npm run lint:frontend`
- [ ] Backend lint passes: `npm run lint:backend`

#### Manual Verification:
- [ ] Merge commit message reviewed by Lex.
- [ ] Spot-check 3 carve-out files: all show "kept our version" (`git log -1 --name-status` for the merge commit lists them under `MM` only — they're committed at our rev, not upstream's).

**Implementation Note:** After Phase 1 complete, pause for Lex's review of `git log -p HEAD~..HEAD` highlights before kicking off Phase 2 (which alters the DB schema). If anything looks lost, this is the moment to amend the merge commit (`git commit --amend`).

---

## Phase 2 — Migration chain integration

### Overview
Wire upstream's 8 new migrations into our existing Alembic chain. Add a no-op merge node parenting our current head and upstream's last new migration.

### Changes Required:

#### 1. Verify the 8 upstream migrations are present after Phase 1

```bash
ls backend/open_webui/migrations/versions/ | grep -E "^(4de81c2a3af1|56359461a091|a0b1c2d3e4f5|a3dd5bedd151|b7c8d9e0f1a2|c1d2e3f4a5b6|d5e6f7a8b9ca|e1f2a3b4c5d6)"
```

Should list:
- `4de81c2a3af1_add_pinned_note_table.py`
- `56359461a091_add_calendar_tables.py`
- `a0b1c2d3e4f5_add_memory_user_id_index.py`
- `a3dd5bedd151_add_tasks_and_summary_to_chat.py`
- `b7c8d9e0f1a2_add_last_read_at_to_chat.py`
- `c1d2e3f4a5b6_add_shared_chat_table.py`
- `d5e6f7a8b9ca_add_automation_tables.py` (renamed in Phase 0)
- `e1f2a3b4c5d6_add_is_pinned_to_note.py`

#### 2. Determine the chain ordering

Inspect each migration's `down_revision` to find the linear order upstream intended. Likely chain (verify from file contents):

```
8452d01d26d7 → 4de81c2a3af1 → e1f2a3b4c5d6 → a0b1c2d3e4f5 → a3dd5bedd151 → b7c8d9e0f1a2 → c1d2e3f4a5b6 → 56359461a091 → d5e6f7a8b9ca
```

(Confirm by reading each file's header; chain order is whichever produces a linear DAG.)

The upstream chain forks off the same `8452d01d26d7` node our customs forked from. That's fine — Alembic supports multi-headed chains via merge nodes.

#### 3. Create the merge node

Generate a new migration (filename + revision):

```bash
cd backend
alembic -c open_webui/alembic.ini revision -m "merge upstream v0.9.5 with custom migrations"
```

This creates `backend/open_webui/migrations/versions/<rev>_merge_upstream_v0_9_5_with_custom_migrations.py`. Edit it so `down_revision` is a **tuple** of our current head and upstream's last new migration:

```python
"""Merge upstream v0.9.5 with custom migrations.

Revision ID: <generated>
Revises: f9a0b1c2d3e4 (our head), d5e6f7a8b9ca (upstream automation, renamed)
Create Date: 2026-05-24 …
"""
revision = "<generated>"
down_revision = ("f9a0b1c2d3e4", "d5e6f7a8b9ca")
branch_labels = None
depends_on = None

def upgrade() -> None:
    pass

def downgrade() -> None:
    pass
```

#### 4. Test the chain on fresh SQLite

```bash
rm -f /tmp/test-fresh.db
DATABASE_URL="sqlite+aiosqlite:////tmp/test-fresh.db" alembic -c backend/open_webui/alembic.ini upgrade head
DATABASE_URL="sqlite+aiosqlite:////tmp/test-fresh.db" alembic -c backend/open_webui/alembic.ini current
```

Expected current = the new merge-node revision.

#### 5. Test the chain on a tenant DB copy

```bash
createdb test_v0_9_5_migration
pg_restore --schema-only -d test_v0_9_5_migration /tmp/tenant-schema-pre-v0.9.5.sql  # or psql -f
DATABASE_URL="postgresql+psycopg://localhost/test_v0_9_5_migration" alembic -c backend/open_webui/alembic.ini upgrade head
DATABASE_URL="postgresql+psycopg://localhost/test_v0_9_5_migration" alembic -c backend/open_webui/alembic.ini current
# Check schema:
psql test_v0_9_5_migration -c "\dt" | grep -E "(automation|calendar|shared_chat|pinned_note)"
dropdb test_v0_9_5_migration
```

#### 6. Verify SQLAlchemy model alignment

Start the backend in dev mode and look for warnings about schema drift:

```bash
DATABASE_URL="sqlite+aiosqlite:////tmp/test-fresh.db" open-webui dev 2>&1 | head -40
```

Look for: "Table 'x' already exists", "Column 'y' has wrong type", "Unknown table 'z'". Resolve any drift by:
- Adding missing columns to models (if our customs need them)
- Creating a follow-up migration if the model expects a column that no migration adds

### Success Criteria:

#### Automated Verification:
- [ ] All 8 upstream migrations present: `ls backend/open_webui/migrations/versions/ | grep -c -E "^(4de81c2a3af1|56359461a091|a0b1c2d3e4f5|a3dd5bedd151|b7c8d9e0f1a2|c1d2e3f4a5b6|d5e6f7a8b9ca|e1f2a3b4c5d6)" | grep -q '^8$'`
- [ ] Merge node migration file exists: `ls backend/open_webui/migrations/versions/*merge_upstream_v0_9_5*.py`
- [ ] `alembic upgrade head` runs cleanly on fresh SQLite (returns exit 0)
- [ ] `alembic upgrade head` runs cleanly on a copy of a tenant Postgres DB (returns exit 0)
- [ ] `alembic current` reports the new merge-node revision
- [ ] `alembic heads` reports exactly one head (the merge node)
- [ ] No "Multiple head revisions" or branch warnings

#### Manual Verification:
- [ ] Backend starts and connects to fresh DB without schema-drift warnings.
- [ ] New tables visible in DB: `automation`, `calendar_event`, `shared_chat`, `pinned_note`.
- [ ] Existing tables intact: `auth`, `user`, `knowledge`, `chat`, `recovery_code`, `agent_config`, `invite`, `user_archive`, `data_warning_log`, `access_grant`, `skill`.

**Implementation Note:** After Phase 2 complete, pause for Lex's confirmation that the tenant-DB-copy test ran clean before moving to Phase 3.

---

## Phase 3 — Dependencies, env, version bump

### Overview
Finalize dependency lockfiles, update `.env.example`, bump `package.json` version, write CHANGELOG entry.

### Changes Required:

#### 1. Regenerate lockfiles

```bash
rm -f package-lock.json uv.lock
npm install
uv sync  # or pip install -e ".[dev]" — match repo convention
git add package-lock.json uv.lock pyproject.toml package.json
```

Verify the backend still imports:

```bash
python -c "import open_webui.main; print('ok')"
```

#### 2. Bump `package.json`

**File:** `package.json`
**Change:** `"version": "0.8.12"` → `"version": "0.9.5"`.

```bash
sed -i '' 's/"version": "0.8.12"/"version": "0.9.5"/' package.json
git add package.json
```

Verify:

```bash
grep '"version"' package.json
```

#### 3. Update `CHANGELOG.md`

**File:** `CHANGELOG.md`
**Change:** add a top Gradient-DS section above upstream's section, describing the merge. Below it, the upstream CHANGELOG already contains v0.9.0–v0.9.5 entries from the merge.

Append (at top, after the `# Changelog` header):

```markdown
## [Gradient-DS v1.2.0] - 2026-05-24

### Merged
- Upstream open-webui v0.9.5 (all v0.9.0–v0.9.5 features, security fixes, async DB refactor, psycopg-v3 driver swap).
- Calendar workspace and Scheduled Automations adopted (env-gated by `ENABLE_CALENDAR` and `ENABLE_AUTOMATIONS`).

### Preserved (Gradient-DS custom)
- TOTP 2FA, GDPR data export + retention + archival, email invites via Microsoft Graph, agent proxy, typed knowledge bases, feature flags, OneDrive/Google Drive/Confluence sync, acceptance modal, sync abstraction layer.

### Gradient-DS default overrides
- (Fill from Phase 0 env-defaults table — any row marked "override".)

### Notes
- Migration: upstream's `d4e5f6a7b8c9_add_automation_tables` was renamed locally to `d5e6f7a8b9ca` to avoid collision with our `d4e5f6a7b8c9_add_soft_delete_columns`.
- Three upstream files were kept at the Gradient version (`routers/retrieval.py`, `MessageInput.svelte`, `InputMenu.svelte`). Security/bugfix hunks audited and ported in PR — see commit log for "[carveout-port]" entries.
```

#### 4. Update `.env.example`

Add all new upstream env vars (under upstream's existing sections) and add a `# === Gradient extensions ===` section at the bottom for our flags. Add `# Gradient default differs from upstream` comments on any rows from the Phase 0 override table.

#### 5. Coordinate with `soev-gitops` (separate repo, not this PR)

File a separate gitops PR — out of scope for this branch — to:
- Verify every tenant's `DATABASE_URL` is psycopg-v3-compatible (no asyncpg-style `?ssl=` params; if present, switch to `?sslmode=`).
- Update Helm chart `open-webui-tenant` to reflect new env vars and any overridden defaults.
- Document in `shared-knowledge/` if any tenant needs special handling.

### Success Criteria:

#### Automated Verification:
- [ ] `package.json` reports `"version": "0.9.5"`: `grep -q '"version": "0.9.5"' package.json`
- [ ] Lockfiles regenerated and committed: `git ls-files package-lock.json uv.lock | wc -l` returns `2`
- [ ] Backend imports cleanly: `python -c "import open_webui.main"`
- [ ] Frontend builds: `npm run build`
- [ ] CHANGELOG.md has the new Gradient-DS v1.2.0 section: `grep -q "\[Gradient-DS v1.2.0\]" CHANGELOG.md`

#### Manual Verification:
- [ ] `.env.example` reviewed — new upstream vars present, Gradient overrides annotated, Gradient extensions section at bottom intact.
- [ ] CHANGELOG section's "Gradient-DS default overrides" populated from Phase 0 table.

---

## Phase 4 — Verification & carve-out audit

### Overview
Run all automated test suites, manually smoke-test every feature, and execute the carve-out audit for the 3 files we kept at `--ours`.

### Changes Required:

#### 1. Run automated test suites

```bash
npm run check
npm run lint:frontend
npm run lint:backend
npm run test:frontend
pytest backend/open_webui/test/ -x  # or whatever pytest entry the repo uses
```

If any fails: fix or document as known-issue. Don't proceed if test failures look caused by the merge.

#### 2. Manual smoke tests — startup & basic flows

Start backend + frontend dev servers (per `CLAUDE.md`):

```bash
open-webui dev &     # backend, port 8080
npm run dev          # frontend, port 5173
```

Verify in order:
- [ ] Login (password)
- [ ] Login (Microsoft Entra OAuth — confirm OAuth 2.1 PKCE didn't break our flow)
- [ ] Login (TOTP-enrolled user)
- [ ] Confluence login (if configured)
- [ ] Send a chat message via OpenAI provider
- [ ] Send a chat message via Ollama provider
- [ ] Send a chat message via our external agent proxy (sk- key auth)
- [ ] Upload a file to a Knowledge Base (typed: local)
- [ ] Sync OneDrive KB — verify one full sync round-trip
- [ ] Sync Google Drive KB — verify one full sync round-trip
- [ ] Sync Confluence KB — verify one full sync round-trip
- [ ] Acceptance modal renders on first login
- [ ] Data export — trigger zip generation, verify Socket.IO ready event, download the zip
- [ ] Email invite — send invite via Graph, accept on a fresh browser

#### 3. Manual smoke tests — new upstream features

- [ ] Calendar workspace loads at `/calendar`
- [ ] Create a calendar event; reminder fires at scheduled time
- [ ] Automation workspace loads at `/automations`
- [ ] Create a recurring automation; verify it runs at the scheduled time and writes to chat history
- [ ] Generative-UI artifact (a tool that emits HTML) renders in chat with the new IFRAME_CSP
- [ ] Profile image upload — verify MIME allowlist works for PNG, rejects SVG

#### 4. Carve-out audit (the key Phase-4 deliverable)

For each of the 3 carved-out files, walk the saved upstream diff and decide per-hunk:

##### `backend/open_webui/routers/retrieval.py`

Read `thoughts/shared/research/v0.9.5-carveout-diffs/backend-open_webui-routers-retrieval.py.diff`. For each upstream hunk, classify:

- [ ] Hunk: Remove unauthenticated `GET /api/v1/retrieval/` status endpoint (`203ec29ba`, #24497) — **PORT** to our file. Remove the endpoint (or add admin auth dependency).
- [ ] Hunk: Collection write-access check on `process_file` and `process_files_batch` (`d11e06f1b`, #24524) — **PORT.** Add the `has_access(user, "write", collection)` check before embedding.
- [ ] Hunk: Offload sync `VECTOR_DB_CLIENT` calls via `AsyncVectorDBClient` (`804f9f315`, #23706) — **PORT.** Wrap our vector calls in the new `AsyncVectorDBClient` (imported from `retrieval/vector/async_client.py`).
- [ ] Hunk: `RAG_RERANKING_BATCH_SIZE` env var support — **PORT** (additive).
- [ ] Hunk: Retrieval source context metadata (resource type/ID in citations) — **PORT** (additive).
- [ ] Hunk: Knowledge collection query access enforcement — **PORT** (security).
- [ ] Hunk: any non-security UX hunks — **decide case-by-case**, default skip.

Commit each ported hunk as a separate commit with subject `[carveout-port] retrieval.py: <description>` so audit trail is preserved.

##### `src/lib/components/chat/MessageInput.svelte`

Read the saved diff. Upstream changes (+78/−6) are mostly:
- [ ] Files-tab attach: browse/attach previously uploaded files — **decide case-by-case**, likely **skip** since we have agent-picker + KB selector covering this.
- [ ] Emoji shortcode menu (colon trigger) — **decide**, likely **skip** (low value vs. merge cost).
- [ ] Swipe-to-reply mobile — **decide**, likely **skip**.
- [ ] Any sanitization or input-validation hunks — **PORT** if any found.

##### `src/lib/components/chat/MessageInput/InputMenu.svelte`

Read the saved diff. Upstream changes (+58/−0) are pure additions, mostly Files tab. **Decide case-by-case**, likely **skip**.

Document each decision in a single commit message: `[carveout-port] MessageInput*.svelte: ported X, skipped Y because Z`.

#### 5. Check redirect-blocking + IFRAME_CSP in real flows

Specifically because they're security-default changes:
- [ ] OneDrive picker still opens (browser fetch → backend OAuth → `services/onedrive/auth.py` httpx — unaffected, sanity-check anyway)
- [ ] Google Drive picker still opens
- [ ] Agent proxy completes a streaming request (aiohttp; verify `allow_redirects` doesn't block our agent service)
- [ ] OpenAI streaming completion works (aiohttp; sanity)
- [ ] Generative-UI artifact iframe renders content under the new IFRAME_CSP (if it breaks, override CSP per Phase 0 step 3 in `config.py`)

### Success Criteria:

#### Automated Verification:
- [ ] `npm run check` returns 0
- [ ] `npm run lint:frontend` returns 0
- [ ] `npm run lint:backend` returns 0
- [ ] `npm run test:frontend` returns 0
- [ ] Backend pytest returns 0
- [ ] Cypress smoke test passes: `npm run cy:open` (or headless equivalent)

#### Manual Verification:
- [ ] All checkboxes in Phase 4 steps 2–5 marked done.
- [ ] Carve-out audit has decisions for every hunk in the 3 saved diffs.
- [ ] Each ported hunk has a `[carveout-port]` commit on the branch.

**Implementation Note:** After Phase 4 complete, pause for Lex's sign-off on the carve-out audit before merging to `dev`.

---

## Phase 5 — Land and document

### Overview
Push branch, open PR, write collab notes, tag fork release.

### Changes Required:

#### 1. Push the branch

```bash
git push -u origin feat/upstream-v0.9.5-merge
```

#### 2. Open PR against `dev`

Title: `Merge upstream v0.9.5 (Calendar + Automations + async DB + security batch)`
Body: link this plan and the inventory research file. Summarize phases, list carve-out decisions, attach the env-defaults table.

#### 3. After merge to `dev`

```bash
git checkout dev
git pull origin dev
git tag -a v1.2.0 -m "Gradient-DS v1.2.0 — upstream v0.9.5 baseline"
git push origin v1.2.0
```

#### 4. Write a collab note

Per `collab/methodology.md`, append to `collab/notes.md` a note summarizing:
- What we did (the merge)
- Key learnings (collision, carve-out strategy, scheduler coexistence, httpx vs aiohttp)
- Open questions (none remaining)
- Related: link this plan + research

Add a row to `collab/index.md` for the note.

#### 5. Update `collab/world/state.md`

Mark the upstream merge work as complete. Note `v1.2.0` as the current internal release.

#### 6. File follow-up tickets

- (Optional) Refactor sync scheduler to use upstream's atomic-claim DB pattern.
- (Optional) Adopt Channels-with-tools streaming.
- (Optional) Adopt new `shared_chats` table for sharing-model unification.
- `soev-gitops` PR to update tenant Helm values for any new/overridden env vars.

### Success Criteria:

#### Automated Verification:
- [ ] Branch pushed: `git ls-remote origin feat/upstream-v0.9.5-merge | grep -q feat/upstream-v0.9.5-merge`
- [ ] After PR merge, tag `v1.2.0` exists: `git tag -l v1.2.0`

#### Manual Verification:
- [ ] PR opened and linked to this plan.
- [ ] Collab note + index entry written.
- [ ] `collab/world/state.md` updated.
- [ ] Follow-up tickets filed.

---

## Testing Strategy

### Unit / Integration Tests
- Frontend: Vitest suite (`npm run test:frontend`). Add at least one unit test for the new `AsyncVectorDBClient` usage if our `services/sync/base_worker.py` integrates it.
- Backend: pytest. Existing fixtures cover most of our custom features; add a smoke test for the new `routers/automations.py` startup if the upstream tests don't already.

### End-to-end Tests
- Cypress: existing suites for registration, chat, documents, settings. Add a new spec `cypress/e2e/calendar.cy.ts` covering: create event, set reminder, verify reminder toast fires (mocked time).

### Manual Testing Steps (also covered in Phase 4)
1. Login flows (password, TOTP, Entra OAuth, Confluence).
2. Chat across providers (OpenAI, Ollama, agent proxy).
3. Cloud sync full round-trip per provider.
4. Calendar create + reminder.
5. Automation create + scheduled run.
6. Data export + retention worker dry-run.
7. Email invite end-to-end.
8. Generative-UI artifact render under new IFRAME_CSP.

## Performance Considerations

- The async DB refactor should improve concurrency under load (no event-loop blocking on file storage, vector search, or knowledge embedding). Watch for regression in:
  - Sync worker throughput (our `base_worker.py` already uses async; upstream's `AsyncVectorDBClient` should help)
  - Bulk file ingestion (Vink-scale tests are still valid)
- psycopg v3's prepared-statement caching differs from asyncpg's. If we see "duplicate prepared statement" errors after deploy, set `psycopg`'s `prepare_threshold` per the upstream Windows fix note in v0.9.3.
- Two schedulers running concurrently (~720 + ~3 queries/hour combined) is well within typical Postgres pool size — no tuning needed.

## Migration Notes

- Tenant DBs: schema migration is forward-only. `alembic upgrade head` adds 8 new tables/columns; nothing is dropped. Migration is safe to run on a live DB (no long table locks expected for `add_table` operations, though `add_column` on `chat`/`auth` will briefly lock — schedule the deploy at low-traffic time).
- Existing TOTP enrollments, agent configs, invites, suspended KBs — all preserved (their migrations are below the new merge node).
- The renamed `d5e6f7a8b9ca_add_automation_tables.py` will be a fresh apply on every tenant; no tenant has an alembic row for `d4e5f6a7b8c9_add_automation_tables` so there's no rename conflict.

## References

- Original research: [`thoughts/shared/research/2026-05-24-open-webui-upstream-v0.9.5-merge-inventory.md`](../research/2026-05-24-open-webui-upstream-v0.9.5-merge-inventory.md)
- Upstream CHANGELOG: `git show upstream/main:CHANGELOG.md`
- Fork's prior upstream-merge precedent: `e5f6a7b8c9d0_merge_upstream_v089.py`
- Sync scheduler implementation: `backend/open_webui/services/sync/scheduler.py:1`
- Upstream's new scheduler: `backend/open_webui/utils/automations.py::scheduler_worker_loop` (after merge)
- Carve-out diffs (created in Phase 1, audited in Phase 4): `thoughts/shared/research/v0.9.5-carveout-diffs/`
- Env-defaults decisions (created in Phase 0): `thoughts/shared/research/v0.9.5-env-defaults.md`

---
