# Open WebUI Upstream Merge Inventory (v0.8.12 → v0.9.5)

**Date:** 2026-05-24
**Author:** @lexlubbers + claude (Opus 4.7 1M)
**Status:** Research — pending decision on what to merge
**Target upstream version:** **v0.9.5** (current upstream/main HEAD)
**Fork base version:** v0.8.12 (merge-base `9bd84258d`)

---

## TL;DR

- We are five upstream minor versions behind: **v0.8.12 → v0.9.5**. Upstream's `v1.x` tags don't exist — those are our fork's internal release labels.
- **557 upstream commits** not in our `dev`, **491 of ours** not in upstream. Net diff: ~892 files, +51K / -154K lines.
- Past full-merge attempts failed because **six upstream files we've heavily customized are also where upstream did its biggest rewrites in this window**: `main.py`, `config.py`, `middleware.py`, `routers/{auths,retrieval,knowledge}.py`, plus the `MessageInput*` Svelte components. Both sides added hundreds of lines independently — there is no clean text merge.
- **Recommended approach:** abandon "full merge". Cherry-pick three coherent slices instead:
  1. **Async DB + ASGI plumbing** (v0.9.0 core refactor + v0.9.2 psycopg v3 driver swap) — mostly in files we never touched, so low conflict, high value.
  2. **All security commits** — ~25 commits / ~60 changelog items spread across v0.9.0/0.9.2/0.9.5. Cherry-pick by SHA, audit, ship.
  3. **A short whitelist of low-risk improvements** (new files only: PaddleOCR-vl loader, Brave LLM Context, `utils/session_pool.py`, `utils/security_headers.py`).
- **Deferred** (case-by-case, separate PRs): Calendar + Automations (whole new feature, big surface area), Channels streaming with tools, Notes pinning, Desktop app integration, Files-tab in chat input.
- **Off-limits** (cannot text-merge — adopt patterns only): `main.py`, `config.py`, `middleware.py`, `MessageInput.svelte`, `InputMenu.svelte`, `routers/{auths,retrieval,knowledge,configs}.py`, `routes/auth/+page.svelte`.

After the cherry-pick slices land, update our reported version to `0.9.5` and re-baseline the merge tracker.

---

## 1. Baseline state

| | |
|---|---|
| Our `package.json` version | **0.8.12** |
| Upstream `package.json` (main) | **0.9.5** |
| Last shared commit (merge-base) | `9bd84258d` (= v0.8.12 tag) |
| Upstream/main HEAD | `3660bc00f` (Merge PR #24492) |
| Upstream commits not in dev | **557** |
| Our commits not in upstream | **491** |
| Files differing | 892 |
| Net diff | +51,650 / −154,532 |

**Important correction during research:** the `v1.0.x` and `v1.1.0` tags visible in this repo (`v1.1.0 → 64f2e1916 Merge pull request #133 from Gradient-DS/test`) are **our fork's internal release tags**, not upstream releases. Upstream's CHANGELOG.md only goes up to v0.9.5 (2026-05-09). When discussing target versions in this doc, "upstream" always means open-webui/open-webui, capped at v0.9.5.

---

## 2. Upstream changes — what we'd be picking up

### v0.9.0 (2026-04-20) — the big bang

The largest single release in this window. Three themes:

**🟡 Async-everywhere refactor.** The maintainers refactored "the core backend database and request paths" to run asynchronously. Concretely: `internal/db.py` rewritten (async session machinery), new `utils/asgi_middleware.py`, new `utils/session_pool.py` (shared aiohttp connection pool), new `retrieval/vector/async_client.py` (`AsyncVectorDBClient` — offloads sync vector clients in async paths, PR #23706), file processing moved off the event loop (`run_in_threadpool` for storage I/O), async vector search, async knowledge embedding to prevent worker thread deadlocks. **This is the "async postgres" the user has been asking for.**

**🔴 Security audit.** ~40 security-tagged items, listed in §3.

**🟢 Big new product surface.**
- **Scheduled Automations** — chat-driven cron, with new tables (`automations`), router (`routers/automations.py`), util (`utils/automations.py`), UI (`routes/(app)/automations/+page.svelte`, `AutomationEditor.svelte`, `ScheduleDropdown.svelte`). Migrations: `d4e5f6a7b8c9_add_automation_tables.py`.
- **Calendar workspace** — events, recurring, reminders, in-app/browser notifications. New tables (`calendar`), router (`routers/calendar.py`), util (`utils/calendar.py`), UI (`routes/(app)/calendar/+page.svelte`, `CalendarView.svelte`, `CalendarEventModal.svelte`). Migration: `56359461a091_add_calendar_tables.py`.
- **Shared chats as first-class** — new table `shared_chats` (`models/shared_chats.py`, migration `c1d2e3f4a5b6_add_shared_chat_table.py`).
- **Tasks/summary fields on chats** — migration `a3dd5bedd151_add_tasks_and_summary_to_chat.py`.
- **`last_read_at` on chats** (unread indicators) — migration `b7c8d9e0f1a2_add_last_read_at_to_chat.py`.
- **Per-user note pinning** — migrations `4de81c2a3af1_add_pinned_note_table.py`, `e1f2a3b4c5d6_add_is_pinned_to_note.py`.
- **Memory index** — migration `a0b1c2d3e4f5_add_memory_user_id_index.py`.
- **Desktop app** released (not our concern — separate Tauri binary; doesn't affect this codebase).
- Azure Responses, Ollama Responses, Mistral TTS, OAuth back-channel logout, configurable security headers (`utils/security_headers.py`).

**Things in v0.9.0 we already do or don't want:**
- TOTP-style 2FA — we have our own (PR #61).
- Data export to CSV — we have our own with zip generation.
- Profile image safety — we'll inherit free.

### v0.9.1 (2026-04-21) — patch

Single-purpose: added missing `aiosqlite` and `asyncpg` to `pyproject.toml` because v0.9.0 broke startup. Will land automatically when we adopt v0.9.0's dep changes.

### v0.9.2 (2026-04-24) — **breaking DB driver swap**

- 🟡 **`asyncpg` → `psycopg[binary]` v3.** Connection-string semantics changed. Any deployment with custom `DATABASE_URL` parameters needs to be reviewed. This is the most important breaking change in the entire window.
- 🔴 **CVE-2025-6176** — Brotli bump.
- 🔴 Model profile image path-traversal sanitization.
- 🟢 PaddleOCR-vl document loader (new file `retrieval/loaders/paddleocr_vl.py`).
- 🟢 Firecrawl v2 API.
- Several niceties: configurable API-key header name, OAuth session disconnect, source overflow indicator, async cancellation cleanup for chat completion, browser-native message virtualization (`content-visibility: auto` replaces our custom DOM culling if we ever had it).

### v0.9.3 (2026-05-09) — quality + performance

50+ improvements. Highlights:
- Voice Mode mute toggle.
- Calendar creation flow (depends on v0.9.0 Calendar — skip if we skip Calendar).
- Assistant message editing (OutputEditView).
- Prompt-list / chat-history loading optimizations (single-query rewrites).
- `{{USER_GROUPS}}` template var.
- Brave LLM Context as web search provider (new file `retrieval/web/brave_llm_context.py`).
- `STT_SKIP_PREPROCESSING`, `STT` chunked worker concurrency limit, PCM TTS → MP3.
- Admin model unload from selector.
- Numerous race/bug fixes in OAuth, MCP cleanup, regeneration, calendar deletion, chat-image capture, multi-worker tool refresh, prompt-tag filtering on non-Latin tags.

### v0.9.4 (2026-05-09) — single bugfix

Chat-scroll regression caused by v0.9.2's `content-visibility` change.

### v0.9.5 (2026-05-09) — security-heavy

~17 security items. The highlights:
- `AIOHTTP_CLIENT_ALLOW_REDIRECTS` env var — block 3xx redirects in all outbound HTTP (SSRF defense). Default off.
- `IFRAME_CSP` — admin-configurable CSP for srcdoc iframes (artifacts, tool embeds, citation modals).
- `PROFILE_IMAGE_ALLOWED_MIME_TYPES` — strict MIME allowlist for profile-image data URIs + `X-Content-Type-Options: nosniff`.
- URL parser SSRF bypass fix (reject `\`, `\t`, `\r`, `\n`).
- File ownership checks before folder/KB attachment.
- Channel message ownership + pin write-permission enforcement.
- Skill/calendar public-sharing permission gates.
- Feedback `user_id` mass-assignment fix (was forgeable).
- Model `params` stripping for read-only users.
- Tool source-code update requires `workspace.tools`/`workspace.tools_import`.
- Removed unauthenticated `GET /api/v1/retrieval/` status endpoint.
- `POST` instead of `GET` for signout.
- Granular markdown rendering controls.

Full bullet list from the upstream changelog is preserved in §A1 (appendix).

---

## 3. Security commits (the must-merge list)

25 commits on the upstream side match security keywords (SSRF, CVE, sanitize, access control, auth, permission, owner, XSS, JWT, bypass, SAML, LDAP, hardcoded, vulnerab). The ones with the broadest blast radius:

| SHA | Version | Subject |
|-----|---------|---------|
| `885454150` | v0.9.5 | prevent redirect-based SSRF in web-fetch and image-load call sites (#24491) |
| `d11e06f1b` | v0.9.5 | prevent redirect-based SSRF and enforce collection write access (#24524) |
| `e7ba8978c` | v0.9.5 | reject parser-confusing chars in `validate_url` to close SSRF bypass (#24534) |
| `f5e110fbe` | v0.9.5 | enforce message ownership in group/DM channel update + delete endpoints (#24506) |
| `d3737176b` | v0.9.5 | require write permission for `pin_channel_message` on standard channels (#24521) |
| `8a0018cf9` | v0.9.5 | gate public sharing of calendars behind `sharing.public_calendars` permission (#24493) |
| `203ec29ba` | v0.9.5 | remove unauthenticated `GET /api/v1/retrieval/` status endpoint (#24497) |
| `804f9f315` | v0.9.0 | offload sync `VECTOR_DB_CLIENT` calls in async paths via `AsyncVectorDBClient` (#23706) |
| `e7ff4768f` | v0.9.0 | add ownership checks to global task endpoints (#23454) |
| `0753409e7` | v0.9.0 | use `ipaddress` stdlib for IPv6 SSRF protection (#23453) |
| `b78dabb44` | v0.9.0 | reject empty passwords in LDAP authentication (#23633) |
| `83024d00b` | v0.9.0 | enforce API key endpoint restrictions at the auth layer, not middleware (#23637) |
| `fb5ef978b` | v0.9.0 | enforce `OAUTH_ALLOWED_DOMAINS` on token exchange endpoint (#23639) |
| `4498c21f4` | v0.9.0 | enforce model access control on Ollama generate/show/embed/embeddings (#23631) |
| `96a0b3239` | v0.9.0 | prevent first-user admin race in LDAP and OAuth registration (#23626) |
| `5eab125f1` | v0.9.0 | sanitize model description HTML with DOMPurify in chat placeholders (#23621) |
| `e790e7be7` | v0.9.0 | enforce model access control on `/responses` endpoint (#23481) |
| `faf935ef5` | v0.9.0 | match JWT expiry on `/auths/add` with other sign-in paths (#23576) |
| `435efa31c` | v0.9.0 | add SSRF protection to OAuth profile picture URL fetching (#23356) |
| `0dd9f462f` | v0.9.0 | feat: oauth backchannel logout |
| `b63da90ae` | v0.9.5 | health probes bypass `CommitSessionMiddleware` for faster response (#24384) |
| `4790faba7` | v0.9.0 | UI: shift+click to bypass message deletion confirmation (#23888) |
| `bf4935818` | v0.9.0 | refactor: use shared `unescapeHtml` in CodeBlock (#23553) |
| `a600f67d6` | v0.9.0 | i18n: fix Chinese translation for Web Upload permission (#23596) |
| `2d83d0f95` | v0.9.0 | perf: replace `unescapeHtml` DOMParser with html-entities decode (#23165) |

This list is from commit subjects. The changelog (§A1) surfaces additional security items that landed inside larger refactor commits — those need to be picked up by file rather than by SHA. Notable additional ones not represented above:

- Iframe CSP (`IFRAME_CSP`) — v0.9.5
- Profile-image MIME allowlist — v0.9.5 (`15e69669` direct commit)
- File ownership on folder/KB attachments — v0.9.5 (`2dbf7b67`)
- Spreadsheet HTML preview sanitization — v0.9.3
- Yandex result parsing guard — v0.9.3
- Webhook avatar URL validation — v0.9.3
- Tool server access-check await fix — v0.9.0
- Per-model access enforcement on `params` for read-only users — v0.9.5
- Public sharing permission gates for channels, models, notes, prompts, tools (`sharing.public_*`) — v0.9.0
- Inactive group members can no longer access channels — v0.9.0
- Azure model name validation/encoding — v0.9.0
- IPv6 SSRF (already in SHA list)
- Brotli CVE-2025-6176 — v0.9.2

**Recommendation:** treat the SHA list as the cherry-pick batch; for the "in larger refactor" items, take the file (`utils/security_headers.py`, `utils/session_pool.py`, helper modules) wholesale, and hand-port the specific checks into the routers we own (`auths.py`, `knowledge.py`, `retrieval.py`).

---

## 4. The conflict map (why a "merge it all" PR keeps failing)

Numbers below are line-level diffs from merge-base `9bd84258d` to (a) `upstream/main` and (b) `origin/dev`. Format: `+adds/-deletes`.

### 🔴 Tier 0 — Cannot text-merge. Adopt patterns only.

| File | Upstream | Ours | Why it's poison |
|---|---|---|---|
| `backend/open_webui/main.py` | +642/−288 | +896/−90 | Both sides rewrote >600 lines independently. We added router mounts (agent_proxy, integrations, confluence_sync, export, invites, totp), scheduler hooks, agent service init. Upstream added automation/calendar router mounts, async lifespan, new middleware, scheduler poll loop, ASGI middleware integration. |
| `backend/open_webui/config.py` | +229/−14 | +704/−9 | We added ~9 feature flags + `PersistentConfig` extensions. Upstream added env vars for scheduler, calendar, automation, security headers, Brave search, MIME allowlist, redirect blocking, terminal proxy headers. Pure additive on both sides — three-way merge with manual block reordering is the only way. |
| `backend/open_webui/utils/middleware.py` | +675/−209 | +163/−11 | Upstream did a massive ASGI rewrite for async (this is core of the async refactor). We added 163 lines of agent/auth/log handling. Tier 0 because upstream's diff is structural — we have to take their version, then re-port our 163 lines as targeted hunks. |
| `backend/open_webui/routers/auths.py` | +175/−77 | +159/−18 | Upstream: OAuth 2.1 PKCE, backchannel logout, JWT expiry alignment, LDAP empty-password rejection, first-user race fix, OAuth allowed-domains on token exchange. Ours: TOTP routes (`/2fa/setup`, `/2fa/verify`, `/2fa/disable`, `/2fa/recovery`), partial-JWT challenge flow, PII stripping. **Both sides touched login flow. High conflict.** |
| `backend/open_webui/routers/retrieval.py` | +149/−119 | +252/−27 | Upstream: removed unauth status endpoint, collection write-access checks on `process_file*`, async-client offload, reranking batch size config, retrieval source metadata. Ours: Confluence search integration, log cleaning, PII stripping in retrieval, streaming improvements for agents. |
| `backend/open_webui/routers/knowledge.py` | +132/−116 | +242/−68 | Upstream: per-file read-access on attach, knowledge collection query permissions, knowledge embedding deadlock fix. Ours: Typed KB (type field + per-type loaders), suspension lifecycle, archival hooks, Confluence integration. Both touched the model around KB types — high conflict. |
| `backend/open_webui/routers/configs.py` | +36/−16 | +615/−7 | We own this — we added 600+ lines for runtime-editable Confluence/OneDrive/GDrive OAuth. Upstream did light updates. Easy to apply upstream as targeted hunks. |
| `src/lib/components/chat/MessageInput.svelte` | +78/−6 | +617/−150 | We rewrote (feature flags, agent picker pills, external agent selector, confluence metadata). Upstream added emoji shortcode menu, files-tab attach, swipe-to-reply. Conflicts are localized. |
| `src/lib/components/chat/MessageInput/InputMenu.svelte` | +58/−0 | +947/−289 | Same story — we did a massive rewrite for feature flags. Upstream added Files tab. Take upstream's Files tab as a hunk. |
| `src/routes/auth/+page.svelte` | (none) | +374/−293 | Upstream didn't touch it (in this window). We added TwoFactorChallenge, partial-JWT flow, Confluence login. Free pass on this one. |
| `src/routes/+layout.svelte` | +157/−54 | +10/−2 | Upstream rewrote theming + WebSocket reconnect indicator. We barely touched it — take upstream wholesale, re-apply our 10 lines. |

### 🟡 Tier 1 — Heavy upstream rewrite, light from us. Take upstream + replay our hunks.

| File | Upstream | Ours | Action |
|---|---|---|---|
| `backend/open_webui/internal/db.py` | +248/−10 | 0 | **The async DB refactor.** Take upstream wholesale. |
| `backend/open_webui/models/users.py` | +216/−183 | +30/0 | Take upstream, replay our 30 lines (archival fields). |
| `backend/open_webui/models/chats.py` | +640/−496 | (check) | Replay any small additions we made. |
| `backend/open_webui/models/files.py` | +108/−89 | +20/0 | Replay 20 lines. |
| `backend/open_webui/models/knowledge.py` | +200/−169 | +266/−20 | Higher conflict — both touched. Tier 0/1 boundary. |
| `backend/open_webui/models/auths.py` | +42/−40 | +78/−3 | TOTP additions on our side; upstream restructured. Need manual reconcile. |
| `backend/open_webui/socket/main.py` | +185/−62 | +58/−31 | Upstream rewrote async handlers + role-invalidation. Replay our 58 lines. |
| `backend/open_webui/env.py` | +160/−18 | +68/−3 | Take upstream, replay our 68 lines (mostly env-var declarations). |
| `backend/open_webui/utils/auth.py` | +81/−38 | +11/−2 | Take upstream. |
| `backend/open_webui/utils/oauth.py` | +461/−133 | (check) | Take upstream (OAuth 2.1 PKCE, backchannel logout). Replay anything we added for Microsoft Graph. |
| `backend/open_webui/tools/builtin.py` | +1122/−128 | (check) | Take upstream (large refactor + new tools). |
| `backend/open_webui/routers/ollama.py` | +286/−476 | (check) | Take upstream (Responses API + access control). |
| `backend/open_webui/routers/openai.py` | +203/−101 | (check) | Take upstream. |
| `backend/open_webui/routers/files.py` | +111/−81 | +53/−1 | Take upstream, replay `DOCUMENT_PROCESSING_TIMEOUT` and cloud-sync file-type detection. |
| `backend/open_webui/routers/channels.py` | +303/−281 | (check) | Take upstream (streaming, message ownership, pin-write permission). |

### 🟢 Tier 2 — Free real estate. Take new upstream files unchanged.

| File | Status |
|---|---|
| `backend/open_webui/utils/asgi_middleware.py` | New upstream — core async plumbing. Take. |
| `backend/open_webui/utils/session_pool.py` | New upstream — shared aiohttp connection pool with safer cleanup. Take. |
| `backend/open_webui/utils/security_headers.py` | New upstream — admin-configurable browser security headers. Take. |
| `backend/open_webui/utils/automations.py` | New — only if we adopt Automations feature. |
| `backend/open_webui/utils/calendar.py` | New — only if we adopt Calendar feature. |
| `backend/open_webui/utils/headers.py` | New — header utilities. Take. |
| `backend/open_webui/utils/response.py` | New — response helpers. Take. |
| `backend/open_webui/retrieval/vector/async_client.py` | New — `AsyncVectorDBClient`. **Required if we adopt async DB.** Take. |
| `backend/open_webui/retrieval/loaders/paddleocr_vl.py` | New — optional, additive. Take. |
| `backend/open_webui/retrieval/web/brave_llm_context.py` | New — optional, additive. Take. |
| `backend/open_webui/retrieval/web/yandex.py` | +2 — trivial guard. Take. |
| `backend/open_webui/models/automations.py` | New — Automations only. |
| `backend/open_webui/models/calendar.py` | New — Calendar only. |
| `backend/open_webui/models/shared_chats.py` | New — shared-chats first-class table. Take if we want unified sharing model. |
| `backend/open_webui/routers/automations.py` | New — Automations only. |
| `backend/open_webui/routers/calendar.py` | New — Calendar only. |
| `backend/open_webui/migrations/versions/*.py` | New migrations — take the ones whose tables we adopt. |
| `backend/open_webui/__init__.py`, `constants.py` | Trivial new modules. Take. |

### 🌍 Tier 3 — Translations / lockfiles / generated.

- `src/lib/i18n/locales/{ko-KR,nl-NL,fil-PH,ta-IN,ru-RU,...}/translation.json` — biggest churn in the whole diff (3378 lines in `ko-KR` alone). Take upstream additions; preserve our nl-NL additions. We've added Dutch strings for our custom features; those will not conflict with upstream new keys (different keys). Use a JSON-aware merge or sort-by-key reformat.
- `package-lock.json`, `uv.lock`, `Dockerfile`, `Makefile` — regenerate / hand-merge.
- `CHANGELOG.md` — take upstream, append our own entries.

### 🛠️ Tier 4 — Our additive subsystems (zero upstream conflict).

All files in these directories exist only on our side — they cannot conflict with upstream:

- `backend/open_webui/services/sync/` (10 files, ~2,473 lines) — sync abstraction layer (`BaseSyncWorker`, `SyncProvider`)
- `backend/open_webui/services/confluence/` (10 files, ~947 lines)
- `backend/open_webui/services/onedrive/` (8 files, ~449 lines)
- `backend/open_webui/services/google_drive/` (8 files, ~575 lines)
- `backend/open_webui/services/deletion/`, `services/retention/`, `services/archival/`, `services/export/`, `services/email/`
- `backend/open_webui/routers/totp.py`, `confluence_sync.py`, `google_drive_sync.py`, `onedrive_sync.py`, `integrations.py`, `invites.py`, `agent_proxy.py`, `agent_configs.py`, `export.py`, `discovery.py`
- `backend/open_webui/utils/agent.py`
- `src/lib/components/admin/Settings/{CloudSync,Integrations,IntegrationProviders,Agents,Acceptance}.svelte`
- `src/lib/utils/features.ts`, `onedrive-file-picker.ts`
- `src/lib/components/workspace/Knowledge/ConfluencePickerModal.svelte`
- `src/lib/components/chat/RagFilter.svelte`
- `helm/open-webui-tenant/`

The only failure mode for these is if upstream introduces a name collision (e.g. an upstream `routers/export.py` would clash with ours). Spot-check during the merge — currently none of these collide.

---

## 5. The "what to actually merge" recommendation

### Phase A — Async DB + ASGI plumbing (minimum target, do first)

Goal: get the async refactor and psycopg-v3 dependency landed. This is the user's stated #1 priority and the biggest forward-compatibility unlock.

1. Take upstream wholesale:
   - `backend/open_webui/internal/db.py`
   - `backend/open_webui/utils/asgi_middleware.py`
   - `backend/open_webui/utils/session_pool.py`
   - `backend/open_webui/utils/headers.py`, `utils/response.py`, `utils/security_headers.py`
   - `backend/open_webui/retrieval/vector/async_client.py`
2. Take upstream `pyproject.toml` deps, then re-add our extras:
   - Adopt `psycopg[binary]` v3, `aiosqlite`, async stack
   - Re-pin: `pyotp`, `qrcode`, our Microsoft Graph / Google libs
   - Drop legacy `asyncpg` only after smoke-test
3. Replay our hunks into upstream's `utils/middleware.py` (only ~163 lines).
4. Adopt upstream's `socket/main.py` async + role-invalidation skeleton; replay our 58 lines on top.
5. Replay 30 lines from us in `models/users.py`, 20 lines in `models/files.py`, 78 lines (TOTP) in `models/auths.py`.
6. **Database driver review** — any deployments with custom `DATABASE_URL` need psycopg-v3-compatible URLs (no `?ssl=` etc. in asyncpg-style; check `genai-utils`/`soev-gitops` env vars). Document in our internal migration runbook.
7. Verify our sync workers (`services/sync/base_worker.py`) work with the new async session machinery — they were already async, but the session factory changed.

**Expected conflict count for Phase A:** localized to `middleware.py`, `socket/main.py`, `pyproject.toml`, `env.py`. All resolvable in an afternoon.

### Phase B — Security batch (do second, ideally same PR)

Cherry-pick all 25 SHAs in §3 plus the bullet-point items not represented as their own SHA. Three sub-batches:

1. **SSRF / URL validation** (`885454150`, `d11e06f1b`, `e7ba8978c`, `0753409e7`, `435efa31c` + Iframe CSP) — mostly localized to `utils/security_headers.py`, `utils/middleware.py`, image-URL handling in chat-rendering, OAuth profile fetching.
2. **Access control** (`f5e110fbe`, `d3737176b`, `8a0018cf9`, `e7ff4768f`, `4498c21f4`, `e790e7be7`, file-ownership on attachments, RAG collection access, public-sharing permission gates) — touches our heavily-customized `routers/{auths,retrieval,knowledge}.py`. Hand-port each as a targeted hunk.
3. **Auth hardening** (`b78dabb44`, `83024d00b`, `fb5ef978b`, `96a0b3239`, `faf935ef5`, `0dd9f462f` backchannel logout) — touches `auths.py`. Hand-port; verify our TOTP middleware still runs in the right order.

### Phase C — Whitelisted small features

Free pickups; new files only, no conflict:

- ✅ PaddleOCR-vl loader (`retrieval/loaders/paddleocr_vl.py`)
- ✅ Brave LLM Context web search (`retrieval/web/brave_llm_context.py`)
- ✅ Yandex parsing guard (2-line patch)
- ✅ Configurable security headers (`utils/security_headers.py`)
- ✅ OAuth back-channel logout (`utils/oauth.py` — already in security batch)
- ✅ Reranking batch size env var
- ✅ `STT_SKIP_PREPROCESSING`, `AIOHTTP_CLIENT_*` env vars

### Phase D — Deferred (case-by-case, separate PRs)

Discuss before merging:

- **Automations + Calendar** — adds 4 new tables, ~3,000+ lines of code, new UI workspaces. Big feature surface, and conflicts with our Scheduled Sync worker semantics (do we want users to schedule their own jobs that hit our sync infrastructure?). **Strong recommendation: defer.**
- **Channels streaming with tools** — touches `routers/channels.py` and `socket/main.py`. We don't currently market Channels to customers; this is upstream's chat-in-channels evolution. Defer.
- **Files tab in chat input** — small UX win, but touches `MessageInput.svelte` and `InputMenu.svelte` which are heavily customized. Defer until we're confident in our agent-picker layer.
- **Mistral TTS, Voice Mode mute, emoji shortcodes, swipe-to-reply, scroll-to-top, OutputEditView** — all frontend polish. Pick up opportunistically after Phase A/B land cleanly.
- **Desktop app** — N/A. The desktop binary is a separate Tauri wrapper; not our deployment model.
- **Shared chats table** — only adopt if we want to unify our `share_id` pattern with upstream's. Otherwise our current pattern keeps working.

### Phase E — Off-limits (forever, or until we re-architect)

Never accept upstream wholesale for:

- `backend/open_webui/main.py`
- `backend/open_webui/config.py`
- `backend/open_webui/routers/auths.py`
- `backend/open_webui/routers/retrieval.py`
- `backend/open_webui/routers/knowledge.py`
- `backend/open_webui/routers/configs.py`
- `src/lib/components/chat/MessageInput.svelte`
- `src/lib/components/chat/MessageInput/InputMenu.svelte`
- `src/routes/auth/+page.svelte`

For these, the merge process is **"read upstream's diff, hand-pick the security/async hunks, re-format our additions to coexist."** Pure text merge will lose work.

---

## 6. Open questions for the user

1. **Do we want Automations + Calendar?** This is the single biggest decision. Adopting them costs ~3K LOC of new code, 4 migrations, a new scheduler-touchpoint that interacts with our sync scheduler, and 2 new admin UI surfaces. **Default: skip.**
2. **Do we adopt upstream's `shared_chats` table** or keep our current sharing model? Adopting requires a data migration for existing shared chats.
3. **`AIOHTTP_CLIENT_ALLOW_REDIRECTS` defaults to off (redirects blocked).** This may break OneDrive/GDrive picker flows that follow 3xx redirects. Need to test before flipping the default for tenants.
4. **`IFRAME_CSP` default** — upstream's default is restrictive. Need to verify our generative-UI artifacts still render under the default policy.
5. **Version bump:** after Phase A+B land, do we update `package.json` to `0.9.5` to truly claim parity, or keep our internal `v1.x` cadence? Recommendation: bump to `0.9.5` so future merges are baselined to a single, real upstream version.
6. **psycopg v3 connection-string review** — does the gitops repo have any tenant with a `DATABASE_URL` that uses asyncpg-specific URL params? Need to grep `soev-gitops/` before deploying.

---

## 7. Suggested merge workflow

1. Create `feat/upstream-v0.9.5-async-and-security` branch off `dev`.
2. **Phase A** as a series of commits, one per category (db.py, middleware.py replay, socket/main replay, pyproject deps, models replay). PR review focused on async-correctness in our sync workers.
3. **Phase B** as a separate PR cherry-picking the 25 security SHAs + hand-ported items. Review against our threat model (data sovereignty), with special attention to: file ownership checks (our cloud KBs surface other users' files), API key bypass (we have sk- keys for agent proxy), OIDC backchannel logout (we use Entra).
4. **Phase C** can ride with Phase B or be a small follow-up.
5. **Update `package.json` to `0.9.5`** + add a CHANGELOG entry naming both fork releases and the upstream baseline.
6. **Defer Phase D**; open a separate discussion ticket per item.

---

## Appendix A1 — Full upstream changelog excerpts (v0.8.12 → v0.9.5)

The exhaustive bullet lists per version are captured during research and live in this file's git history. For brevity here, see upstream `CHANGELOG.md` at `upstream/main`:

```
git show upstream/main:CHANGELOG.md
```

Categorized version-by-version output from research is preserved verbatim in this conversation's session notes — propose a follow-up to consolidate it into `collab/docs/upstream-v0.9.5-changelog.md` if we want a permanent in-repo copy.

## Appendix A2 — Files I haven't classified

Some files have non-trivial diffs on both sides but weren't critical enough to call out. If/when we proceed, run the same `git diff --numstat 9bd84258d..upstream/main` and walk anything >150 lines on both sides. Examples:

- `src/lib/components/chat/Chat.svelte` (+525/−275 upstream — message virtualization rewrite)
- `src/lib/components/chat/ModelSelector/Selector.svelte` (+407/−314 upstream — selector layout/focus rework)
- `src/lib/components/layout/Sidebar.svelte` (+309/−115 upstream — unread indicators)
- `src/lib/components/admin/Evaluations/Feedbacks.svelte` (+377/−225 upstream — CSV export, model filtering)
- `backend/open_webui/utils/tools.py` (+236/−72 upstream — async tool listing await fix)

These are all "Tier 1" — take upstream, replay our hunks if any.

---

## Appendix A3 — Source data

- Merge-base sha: `9bd84258d09eefe7bf975878fb0e31a5dadfe0f8` (= `v0.8.12`)
- Upstream/main HEAD: `3660bc00f`
- Upstream/main package.json version: `0.9.5`
- Counts: 557 upstream / 491 ours / 892 files / +51,650 / −154,532
- Security-flavored commit count by SHA grep: 25
- Total upstream commits with `refac` prefix: 378 (69%) — note that these are often substantive refactors with terse messages, not no-ops.

---
