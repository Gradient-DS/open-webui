---
date: 2026-05-06T21:10:47+02:00
researcher: Lex Lubbers
git_commit: 043f66bf29b6a9c55433a8dd301423e54b165989
branch: dev
repository: open-webui (soev fork)
topic: "Locked-down .env configuration for sensitive Docker Compose deployment with agents package"
tags: [research, deployment, env-config, feature-flags, security-hardening, persistent-config, audit-logging, admin-permissions]
status: complete
last_updated: 2026-05-06
last_updated_by: Lex Lubbers
---

# Research: Locked-down .env configuration for sensitive Docker Compose deployment

**Date**: 2026-05-06T21:10:47+02:00
**Researcher**: Lex Lubbers
**Git Commit**: 043f66bf29b6a9c55433a8dd301423e54b165989
**Branch**: dev
**Repository**: open-webui (soev fork)

## Research Question

Deploying a locked-down instance of this Open WebUI fork (with the agents package) on a Docker Compose stack in a sensitive Dutch public-sector environment. Need:

1. A complete `.env` file disabling: changelog popup, prompt suggestions, version-update toast, all "+" input menu options, model builder, knowledge bases, files, notes, prompts, tools, folders, playground, voice/audio, advanced parameters, system prompt, personalization tab, audio tab, temporary chat, control pane, follow-up prompts.
2. Settings tabs trimmed to General (without system prompt / advanced params), Interface (only UI/Chat/Input subsections), Data Controls, Account, About.
3. `ENABLE_PERSISTENT_CONFIG=False` and `RESET_CONFIG_ON_START=True` for setup.
4. Admins must NOT be able to read other users' chats.
5. Proper audit logging.
6. General hardening pointers for sensitive deploy.

## Summary

This fork uses **three independent toggle layers** that must all be considered:

| Layer | Prefix | Visible in `/api/config`? | Admin bypasses? | DB-overridable? |
|---|---|---|---|---|
| Top-level features | `ENABLE_*` | yes | sometimes | yes (PersistentConfig) |
| SaaS-tier kill switches (this fork) | `FEATURE_*` | yes | **no** | no (env-only) |
| Per-role grants | `USER_PERMISSIONS_*` | indirectly via `/api/v1/users/permissions` | yes | yes |

Most of what you want is achieved by setting `FEATURE_*` flags (env-only, no admin bypass) plus a few `ENABLE_*` flags. To eliminate ambiguity during setup, run with `ENABLE_PERSISTENT_CONFIG=False, RESET_CONFIG_ON_START=True` — env vars become the sole source of truth and the DB config table is wiped on every boot.

For admin-cannot-read-user-chats: set `ENABLE_ADMIN_CHAT_ACCESS=False` (gates `/api/v1/chats/list/user/{user_id}` and the `share_id` admin-bypass branch) and `ENABLE_ADMIN_EXPORT=False` (gates DB dump endpoints).

For audit logging: set `AUDIT_LOG_LEVEL=METADATA` (or `REQUEST`), keep `ENABLE_AUDIT_LOGS_FILE=True`, and override `AUDIT_EXCLUDED_PATHS` since the default excludes `/chats,/chat,/folders` — chat mutations would otherwise be silently dropped.

For sensitive deploy: enable `OFFLINE_MODE=true`, harden cookies, set CSP/HSTS headers, lock down CORS, require 2FA, disable community sharing, restrict file uploads, disable pip frontmatter installs.

---

## Recommended `.env` (drop-in)

The variables below are grouped and annotated. Where I write a comment with `(PC)`, that's a PersistentConfig — env wins only when `ENABLE_PERSISTENT_CONFIG=False`.

```bash
###############################################################################
# 0. Persistent Config — start strict, switch later
###############################################################################
ENABLE_PERSISTENT_CONFIG=False
RESET_CONFIG_ON_START=True
# When stable, switch to: ENABLE_PERSISTENT_CONFIG=True, RESET_CONFIG_ON_START=False
# WARNING: with persistent=True, admin UI changes will OVERRIDE env vars on subsequent boots.

###############################################################################
# 1. Hide UI surfaces (FEATURE_* — env-only, no admin bypass)
###############################################################################
# Changelog "What's New" modal (admin-only, but still ugly)
FEATURE_CHANGELOG=False

# Hide entire chat input "+" menu (file/url/reference/screen capture/etc)
FEATURE_INPUT_MENU=False
# Belt-and-suspenders for individual items (still useful if you re-enable the menu)
FEATURE_WEBPAGE_URL=False
FEATURE_REFERENCE_CHATS=False
FEATURE_CAPTURE=False
FEATURE_KNOWLEDGE=False
FEATURE_TOOLS=False
FEATURE_BUILTIN_TOOLS=False
FEATURE_TOOL_SERVERS=False
FEATURE_TERMINAL_SERVERS=False

# Workspace tabs
FEATURE_MODELS=False        # model builder
FEATURE_PROMPTS=False
# (knowledge & tools already covered above)

# Other workspace/admin
FEATURE_PLAYGROUND=False
FEATURE_VOICE=False         # hides mic, call, TTS, Voice subsection in Interface, Audio tab
FEATURE_TEMPORARY_CHAT=False
FEATURE_CHAT_CONTROLS=False # right-side controls pane (system prompt / params / valves / files)
FEATURE_SYSTEM_PROMPT=False # extra: hides system prompt in user General tab too
FEATURE_ARTIFACTS=False     # hides Artifacts subsection in Interface settings
FEATURE_DOCUMENT_WRITER=False
FEATURE_AGENT_PICKER=False  # leave False unless you want users to pick agents
FEATURE_SKILLS=False
FEATURE_USER_DEMOGRAPHICS=False
FEATURE_NOTES_AI_CONTROLS=False
FEATURE_CHAT_OVERVIEW=False

# Custom-fork: explicitly disable admin features you don't need
FEATURE_ADMIN_EVALUATIONS=False
FEATURE_ADMIN_FUNCTIONS=False
# FEATURE_ADMIN_SETTINGS=True   # keep True (you need admin settings)
# FEATURE_ADMIN_SETTINGS_TABS=  # optional whitelist of admin tabs

###############################################################################
# 2. Hide UI surfaces (ENABLE_* — PersistentConfig, only authoritative when persistent_config=False)
###############################################################################
# Folders in chat sidebar
ENABLE_FOLDERS=False

# Notes feature
ENABLE_NOTES=False

# Suggestions chips on new-chat page (no kill-switch, set to empty list)
DEFAULT_PROMPT_SUGGESTIONS=[]

# Bottom-right "new version available" toast
ENABLE_VERSION_UPDATE_CHECK=False

# Follow-up prompts after assistant response
ENABLE_FOLLOW_UP_GENERATION=False

# Memories / personalization tab
ENABLE_MEMORIES=False

# Direct external connections (Connections tab in user settings)
ENABLE_DIRECT_CONNECTIONS=False

# Community sharing of models/prompts
ENABLE_COMMUNITY_SHARING=False

# Other safe-to-disable
ENABLE_MESSAGE_RATING=False        # users won't rate messages
ENABLE_EVALUATION_ARENA_MODELS=False
ENABLE_USER_WEBHOOKS=False
ENABLE_CHANNELS=False              # channels = group chat feature
ENABLE_CODE_INTERPRETER=False
ENABLE_CODE_EXECUTION=False
ENABLE_IMAGE_GENERATION=False
ENABLE_WEB_SEARCH=False
# ENABLE_RAG_HYBRID_SEARCH=...     # only if you use RAG; otherwise irrelevant

###############################################################################
# 3. Per-user permissions — belt-and-suspenders for non-admin users
###############################################################################
# Workspace tabs (admins still bypass these in code)
USER_PERMISSIONS_WORKSPACE_MODELS_ACCESS=False
USER_PERMISSIONS_WORKSPACE_KNOWLEDGE_ACCESS=False
USER_PERMISSIONS_WORKSPACE_PROMPTS_ACCESS=False
USER_PERMISSIONS_WORKSPACE_TOOLS_ACCESS=False

# Chat features
USER_PERMISSIONS_CHAT_TEMPORARY=False
USER_PERMISSIONS_CHAT_TEMPORARY_ENFORCED=True
USER_PERMISSIONS_CHAT_CONTROLS=False        # right pane
USER_PERMISSIONS_CHAT_SYSTEM_PROMPT=False
USER_PERMISSIONS_CHAT_PARAMS=False          # advanced parameters in General tab
USER_PERMISSIONS_CHAT_VALVES=False
USER_PERMISSIONS_CHAT_FILE_UPLOAD=False
USER_PERMISSIONS_CHAT_STT=False
USER_PERMISSIONS_CHAT_TTS=False
USER_PERMISSIONS_CHAT_CALL=False

# Feature toggles
USER_PERMISSIONS_FEATURES_FOLDERS=False
USER_PERMISSIONS_FEATURES_NOTES=False
USER_PERMISSIONS_FEATURES_MEMORIES=False
USER_PERMISSIONS_FEATURES_WEB_SEARCH=False
USER_PERMISSIONS_FEATURES_IMAGE_GENERATION=False
USER_PERMISSIONS_FEATURES_CODE_INTERPRETER=False
USER_PERMISSIONS_FEATURES_DIRECT_TOOL_SERVERS=False

# Settings tabs (Interface tab itself stays on by default; we trim subsections via FEATURE_* above)

###############################################################################
# 4. Admin chat privacy (CRITICAL for tenant-isolated admins)
###############################################################################
ENABLE_ADMIN_CHAT_ACCESS=False         # blocks /chats/list/user/{id} and admin share-id bypass
ENABLE_ADMIN_EXPORT=False              # blocks /chats/all/db, /utils/db/download, /utils/db/export
ENABLE_ADMIN_WORKSPACE_CONTENT_ACCESS=False
BYPASS_ADMIN_ACCESS_CONTROL=False      # admin must respect ACLs on KBs/models
# NOTE: there is no admin "login as user" feature (verified — no impersonation in this codebase).
# NOTE: admins still have DB-level access by virtue of running the deployment.

###############################################################################
# 5. Audit logging
###############################################################################
GLOBAL_LOG_LEVEL=INFO
LOG_FORMAT=json                        # structured JSON for log shippers
ENABLE_AUDIT_LOGS_FILE=True
ENABLE_AUDIT_STDOUT=True               # also emit to stdout for k8s/docker log capture
AUDIT_LOGS_FILE_PATH=/app/backend/data/audit.log
AUDIT_LOG_FILE_ROTATION_SIZE=50MB
AUDIT_LOG_LEVEL=REQUEST                # NONE|METADATA|REQUEST|REQUEST_RESPONSE
MAX_BODY_LOG_SIZE=4096

# Default excludes /chats,/chat,/folders — REMOVE this default to capture chat mutations
AUDIT_EXCLUDED_PATHS=
# Or whitelist explicit paths instead:
# AUDIT_INCLUDED_PATHS=/api/v1/auths,/api/v1/chats,/api/v1/users,/api/v1/configs

# Optional OTEL forwarding for centralized observability
# ENABLE_OTEL=true
# ENABLE_OTEL_LOGS=true
# OTEL_EXPORTER_OTLP_ENDPOINT=https://otel.internal.example/v1/logs

###############################################################################
# 6. Authentication & sessions
###############################################################################
WEBUI_AUTH=True
WEBUI_SECRET_KEY=__GENERATE_64_BYTE_RANDOM__
JWT_EXPIRES_IN=8h
WEBUI_SESSION_COOKIE_SAME_SITE=strict
WEBUI_SESSION_COOKIE_SECURE=True
WEBUI_AUTH_COOKIE_SAME_SITE=strict
WEBUI_AUTH_COOKIE_SECURE=True
ENABLE_SIGNUP=False
ENABLE_INITIAL_ADMIN_SIGNUP=False      # set True only on very first boot, then back to False
ENABLE_LOGIN_FORM=True                 # set False if SSO-only
DEFAULT_USER_ROLE=pending              # admin must approve every signup
ENABLE_API_KEYS=False
ENABLE_PASSWORD_VALIDATION=True
ENABLE_FORWARD_USER_INFO_HEADERS=False
BYPASS_MODEL_ACCESS_CONTROL=False

# 2FA (highly recommended for sensitive deploy)
ENABLE_2FA=True
REQUIRE_2FA=True
TWO_FA_GRACE_PERIOD_DAYS=7

###############################################################################
# 7. Network / CORS / security headers
###############################################################################
WEBUI_URL=https://chat.your-tenant.example.gov.nl
CORS_ALLOW_ORIGIN=https://chat.your-tenant.example.gov.nl
HSTS=max-age=31536000;includeSubDomains;preload
CONTENT_SECURITY_POLICY=default-src 'self'; img-src 'self' data: blob:; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; font-src 'self' data:; frame-ancestors 'none';
XFRAME_OPTIONS=DENY
XCONTENT_TYPE=nosniff
REFERRER_POLICY=no-referrer
PERMISSIONS_POLICY=accelerometer=(),camera=(),microphone=(),geolocation=(),payment=()
XPERMITTED_CROSS_DOMAIN_POLICIES=none
XDOWNLOAD_OPTIONS=noopen
CACHE_CONTROL=no-store, max-age=0

###############################################################################
# 8. Outbound calls / data egress
###############################################################################
OFFLINE_MODE=True                      # forces ENABLE_VERSION_UPDATE_CHECK=False, HF_HUB_OFFLINE=1
RAG_EMBEDDING_MODEL_AUTO_UPDATE=False
RAG_RERANKING_MODEL_AUTO_UPDATE=False
WHISPER_MODEL_AUTO_UPDATE=False
ENABLE_PIP_INSTALL_FRONTMATTER_REQUIREMENTS=False  # CRITICAL: blocks tool/function pip auto-install
ENABLE_RAG_LOCAL_WEB_FETCH=False                   # SSRF gate
ANONYMIZED_TELEMETRY=False
DO_NOT_TRACK=1
SCARF_NO_ANALYTICS=true

###############################################################################
# 9. RAG / file upload safety (only matters if you ever re-enable file uploads)
###############################################################################
RAG_FILE_MAX_SIZE=50
RAG_FILE_MAX_COUNT=20
RAG_ALLOWED_FILE_EXTENSIONS=pdf,docx,xlsx,pptx,txt,md,csv

###############################################################################
# 10. Data retention / GDPR (custom soev fork features)
###############################################################################
ENABLE_DATA_EXPORT=True                # GDPR right of access (self-export)
DATA_EXPORT_RETENTION_HOURS=24
ENABLE_USER_ARCHIVAL=True
ENABLE_DATA_WARNINGS=True
DATA_RETENTION_TTL_DAYS=730            # adjust per legal policy
USER_INACTIVITY_TTL_DAYS=180
CHAT_RETENTION_TTL_DAYS=365
KNOWLEDGE_RETENTION_TTL_DAYS=730
DATA_RETENTION_WARNING_DAYS=30
ENABLE_RETENTION_WARNING_EMAIL=True    # requires Microsoft Graph mail config
ENABLE_AUTO_ARCHIVE_ON_SELF_DELETE=True
DEFAULT_ARCHIVE_RETENTION_DAYS=1095    # 3 years (ISO 27001 default)
AUTO_ARCHIVE_RETENTION_DAYS=365

###############################################################################
# 11. Agent integration (this fork's external agent API)
###############################################################################
# Internal chat-routing to agents (set True if you want users to chat with agents):
AGENT_API_ENABLED=True
AGENT_API_BASE_URL=http://agents:3080  # internal docker-compose service URL
AGENT_API_KEY=__STRONG_SHARED_SECRET__
# AGENT_API_AGENTS=agent-id-1,agent-id-2  # whitelist
# Reverse-proxy at /api/v1/agent/ for EXTERNAL sk- callers (separate feature):
ENABLE_AGENT_PROXY=False               # leave False unless external apps will call this WebUI as an OpenAI-compatible gateway

###############################################################################
# 12. Optional: Redis (recommended for HA + rate limiting across replicas)
###############################################################################
# REDIS_URL=redis://redis:6379/0
# WEBSOCKET_MANAGER=redis
```

---

## Detailed Findings

### 1. Hiding UI surfaces — `FEATURE_*` vs `ENABLE_*`

This fork distinguishes between two top-level toggle styles:

- **`FEATURE_*`** — env-only kill switches that admins do NOT bypass. Defined `config.py:1817-1849`. Frontend reads via `isFeatureEnabled()` in `src/lib/utils/features.ts:35`. These are the SaaS-tier flags your fork added — perfect for "this customer doesn't get this".
- **`ENABLE_*`** — typically `PersistentConfig`, admin-overridable from the UI. Some are env-only.

Per requested feature:

| Requested | Var | Type | File:line | Default |
|---|---|---|---|---|
| Hide changelog/release-notes modal | `FEATURE_CHANGELOG` | env-only | `config.py:1825` | `True` |
| Hide input suggestions | `DEFAULT_PROMPT_SUGGESTIONS` (set to `[]`) | PC | `config.py:1225-1229` | built-in list |
| Hide "new version" toast | `ENABLE_VERSION_UPDATE_CHECK` | env-only | `env.py:857` | `True` |
| Hide entire `+` input menu | `FEATURE_INPUT_MENU` | env-only | `config.py:1834` | `True` |
| Disable model builder | `FEATURE_MODELS` | env-only | `config.py:1827` | `True` |
| Disable knowledge bases | `FEATURE_KNOWLEDGE` | env-only | `config.py:1828` | `True` |
| Disable files (workspace tab) | _no env var exists_ | — | — | tab not registered in this fork |
| Disable notes | `ENABLE_NOTES` | PC | `config.py:1588` | `True` |
| Disable prompts | `FEATURE_PROMPTS` | env-only | `config.py:1829` | `True` |
| Disable tools | `FEATURE_TOOLS` (+ `FEATURE_BUILTIN_TOOLS`, `FEATURE_TOOL_SERVERS`, `FEATURE_TERMINAL_SERVERS`) | env-only | `config.py:1830,1840,1836,1837` | `True/True/False/False` |
| Disable folders | `ENABLE_FOLDERS` | PC | `config.py:1568` | `True` |
| Disable playground | `FEATURE_PLAYGROUND` | env-only | `config.py:1821` | `True` |
| Disable voice (mic + TTS) | `FEATURE_VOICE` | env-only | `config.py:1824` | `True` |
| Disable temporary chat | `FEATURE_TEMPORARY_CHAT` | env-only | `config.py:1835` | `True` |
| Disable control pane | `FEATURE_CHAT_CONTROLS` | env-only | `config.py:1817` | `True` |
| Disable follow-up prompts | `ENABLE_FOLLOW_UP_GENERATION` | PC | `config.py:2094` | `True` |

Frontend wiring is consistent: each `FEATURE_*` is exposed in `/api/config` (`backend/open_webui/main.py:2591-2705`), read by `src/lib/utils/features.ts`, and gates a route layout or component conditional. For example, `FEATURE_PLAYGROUND` gates `routes/(app)/playground/+layout.svelte:14`; `FEATURE_VOICE` gates `MessageInput.svelte:2237` (mic), `:2288` (call), and `Settings/Interface.svelte:1290` (Voice subsection).

### 2. User Settings modal — trimming tabs and subsections

Tab visibility logic lives in `src/lib/components/chat/SettingsModal.svelte:482-513`. Each tab is conditionally listed:

| Tab | Condition | How to hide |
|---|---|---|
| General (Algemeen) | always | cannot hide |
| Interface | admin OR `permissions.settings.interface` | keep on |
| Connections | `enable_direct_connections` | `ENABLE_DIRECT_CONNECTIONS=False` |
| Tools | (`FEATURE_TOOL_SERVERS` OR `FEATURE_TERMINAL_SERVERS`) AND permission | both False (already done) |
| Personalization | `enable_memories` AND permission | `ENABLE_MEMORIES=False` |
| Audio | `FEATURE_VOICE` | `FEATURE_VOICE=False` (already done) |
| Data Controls | always | keep on |
| Account | always | keep on |
| About | always | keep on |

**General tab** — `Settings/General.svelte`:
- System Prompt block (`:286-303`) wrapped in `FEATURE_SYSTEM_PROMPT` AND (`admin OR permissions.chat.system_prompt`). To hide for non-admins: `USER_PERMISSIONS_CHAT_SYSTEM_PROMPT=false`. To also hide for admins: `FEATURE_SYSTEM_PROMPT=false`.
- Advanced Parameters block (`:305-325`): `admin OR (permissions.chat.controls AND permissions.chat.params)`. No global flag — hide for non-admins via `USER_PERMISSIONS_CHAT_PARAMS=false`. **Admins always see this** unless you patch the component.

**Interface tab** — `Settings/Interface.svelte`:
- UI / Chat / Input / File subsections have no internal wrappers — they show unconditionally.
- Artifacts (`:1206-1265`) gated by `FEATURE_ARTIFACTS`.
- Document Writer (`:1267-1288`) gated by `FEATURE_DOCUMENT_WRITER`.
- Voice (`:1290-1330`) gated by `FEATURE_VOICE`.
- Setting `FEATURE_ARTIFACTS=False`, `FEATURE_DOCUMENT_WRITER=False`, `FEATURE_VOICE=False` reduces Interface tab to UI/Chat/Input/File. (You can't hide File without code change.)

**Personalization** — hide via `ENABLE_MEMORIES=False` (hides for everyone) or `USER_PERMISSIONS_FEATURES_MEMORIES=false` (non-admins only).

**Audio** — hidden when `FEATURE_VOICE=False`.

### 3. Admin can't read user chats

The admin "browse user chats" surface is the user list in `Admin > Users` — each row has a "Chats" button (`src/lib/components/admin/Users/UserList.svelte:507`) that opens `UserChatsModal.svelte`, which calls `getChatListByUserId` → `GET /api/v1/chats/list/user/{user_id}` (`routers/chats.py:513-543`). That endpoint enforces `if not ENABLE_ADMIN_CHAT_ACCESS: raise 401` (`chats.py:523-527`).

Setting `ENABLE_ADMIN_CHAT_ACCESS=False`:
- Hides the "Chats" button on the user list (`UserList.svelte:507` checks `$config.features.enable_admin_chat_access`).
- 401's the per-user list endpoint.
- Forces `chats.py:823-826` to use the share-id-only branch (admins can't fetch a chat by id pretending it's a share id).

Also set `ENABLE_ADMIN_EXPORT=False` to block:
- `GET /api/v1/chats/all/db` (full chat DB dump, `chats.py:709-716`).
- `GET /api/v1/utils/db/download` (raw SQLite download, `routers/utils.py:215-233`).
- `GET /api/v1/utils/db/export` (JSON DB export, `routers/utils.py:236+`).

There is **no admin "log in as user" / impersonation feature** in this codebase (verified). Admins still have direct DB access by virtue of running the deployment — these flags only constrain the HTTP API surface. If true tenant isolation matters, run separate stacks per tenant.

### 4. Audit logging

Implemented as ASGI middleware (`backend/open_webui/utils/audit.py`) writing to a Loguru-managed file.

Key configuration:
- `AUDIT_LOG_LEVEL` (`env.py:885`, default `NONE`) — must be set to one of `METADATA`, `REQUEST`, `REQUEST_RESPONSE` to enable the middleware (`main.py:1993-2000`).
- `AUDIT_LOGS_FILE_PATH` defaults to `{DATA_DIR}/audit.log` (`env.py:875`).
- Rotation: `AUDIT_LOG_FILE_ROTATION_SIZE=10MB` default, zip-compressed.
- **Important caveat**: `AUDIT_EXCLUDED_PATHS` defaults to `/chats,/chat,/folders` (`env.py:892`) — chat mutations are NOT audited out of the box. Override to empty (or use `AUDIT_INCLUDED_PATHS` whitelist) to capture them.
- Only audits `POST/PUT/PATCH/DELETE` (`audit.py:120,205`) — GETs (including `/chats/list/user/{id}`) are NOT captured. Login/logout/signup are always logged regardless.
- Passwords are redacted (`audit.py:259-265`). User identity in entries is `id+role` only (`:254`).

For OTEL forwarding to a central observability stack: `ENABLE_OTEL=true`, `ENABLE_OTEL_LOGS=true`, `OTEL_EXPORTER_OTLP_ENDPOINT=...` (`env.py:907-914`).

### 5. Persistent config behavior

`backend/open_webui/config.py:170-187` defines `PersistentConfig.__init__`. On every read:

```
if config_value is not None
   AND ENABLE_PERSISTENT_CONFIG=True
   AND (key not in oauth.* OR ENABLE_OAUTH_PERSISTENT_CONFIG=True):
   value = DB
else:
   value = ENV (or hardcoded default)
```

`RESET_CONFIG_ON_START=True` runs `db.query(Config).delete()` at startup (`main.py:827-828`). It empties the config table, so all subsequent `PersistentConfig` reads fall to the env path.

**Recommendations**:
- During setup: `ENABLE_PERSISTENT_CONFIG=False, RESET_CONFIG_ON_START=True`. Env wins always; DB is wiped each boot.
- After stable: switch to `ENABLE_PERSISTENT_CONFIG=True, RESET_CONFIG_ON_START=False` ONLY IF you want admins to be able to override settings via UI. **Be aware**: any UI change persists and overrides the env value on next boot. For a strictly-locked deployment, keep `ENABLE_PERSISTENT_CONFIG=False` permanently.

### 6. Sensitive-environment hardening (additional)

Beyond the .env above:

**Auth chain**:
- `WEBUI_SECRET_KEY` — generate cryptographically random ≥64 bytes.
- `JWT_EXPIRES_IN=8h` (default `4w` is too long for sensitive deploy).
- Cookies: `SAMESITE=strict`, `SECURE=true`.
- `ENABLE_SIGNUP=False`, `DEFAULT_USER_ROLE=pending`.
- `ENABLE_API_KEYS=False` unless required; if enabled, set `ENABLE_API_KEYS_ENDPOINT_RESTRICTIONS=True` and `API_KEYS_ALLOWED_ENDPOINTS`.
- `ENABLE_PASSWORD_VALIDATION=True` enforces complexity (regex at `env.py:482-484`).

**Login brute-force**: hardcoded `RateLimiter(15, 180s)` at `routers/auths.py:96`, Redis-backed. Configure `REDIS_URL` to make it work across replicas.

**SSO/OAuth** (recommended over password): `ENABLE_OAUTH_SIGNUP=True`, `ENABLE_LOGIN_FORM=False`, `OAUTH_MERGE_ACCOUNTS_BY_EMAIL=False` (account-takeover prevention), `OAUTH_BLOCKED_GROUPS` for deny lists, `OAUTH_ALLOWED_ROLES`/`OAUTH_ADMIN_ROLES` mapping.

**Egress**: `OFFLINE_MODE=true` is the big one — disables version check, HF auto-downloads, and various phone-home behaviors. Combine with `ENABLE_PIP_INSTALL_FRONTMATTER_REQUIREMENTS=False` — this is critical because tools/functions can declare pip requirements in frontmatter and Open WebUI will install them at runtime, which is a code-execution surface.

**Network/headers**: set CSP, HSTS, X-Frame-Options=DENY, Permissions-Policy, etc. via env vars (read at startup by `utils/security_headers.py:36-81`).

**SSRF**: `ENABLE_RAG_LOCAL_WEB_FETCH=False` blocks the local-fetch path in `retrieval/web/utils.py:80`.

**TLS**: terminate at the reverse proxy (Traefik/nginx). Set `WEBUI_URL` to canonical origin. Pin `CORS_ALLOW_ORIGIN` (default `*` is unsafe — see warning at `config.py:1926-1927`).

**Data sovereignty**: this fork has the DPIA features (`ENABLE_DATA_EXPORT`, `DATA_RETENTION_TTL_DAYS`, `ENABLE_USER_ARCHIVAL`) — use them. They're documented in [DPIA Compliance: User Data Export](../../../collab/index.md) and [Configurable Data Retention](../../../collab/index.md) (see soev `index.md` 31-03-2026 entries).

**Audit log shipping**: send `audit.log` to a SIEM (rsyslog forwarder, Loki, Splunk, etc.). Loguru emits structured JSON when `LOG_FORMAT=json`.

---

## Code References

- `backend/open_webui/config.py:167` — `ENABLE_PERSISTENT_CONFIG`
- `backend/open_webui/config.py:170-187` — `PersistentConfig` resolution rule
- `backend/open_webui/config.py:1568, 1588, 1710, 1817-1849, 2094` — feature flag definitions
- `backend/open_webui/env.py:394, 857, 869-900, 948-953` — `RESET_CONFIG_ON_START`, version check, audit, pip
- `backend/open_webui/main.py:1993-2000, 2591-2705, 827-828` — audit middleware, /api/config payload, lifespan reset
- `backend/open_webui/routers/chats.py:513-543, 673-716, 818-829` — admin chat access endpoints
- `backend/open_webui/routers/auths.py:96, 629` — login rate limiter, 2FA enforcement
- `backend/open_webui/utils/audit.py:36-49, 106-265` — audit middleware logic
- `backend/open_webui/utils/logger.py:48-193` — Loguru configuration
- `backend/open_webui/utils/security_headers.py:36-150` — security header env vars
- `src/lib/components/chat/SettingsModal.svelte:482-513` — settings tabs visibility
- `src/lib/components/chat/Settings/General.svelte:286-325` — system prompt + advanced params guards
- `src/lib/components/chat/Settings/Interface.svelte:337-1330` — Interface subsections
- `src/lib/utils/features.ts:35-216` — `isFeatureEnabled()`, `hasFeatureAccess()`, `isChatControlSectionEnabled()`
- `src/lib/components/admin/Users/UserList.svelte:507` — admin chats button conditional

## Architecture Insights

- The fork's `FEATURE_*` system is a clean SaaS-tier kill-switch layer that does NOT bypass admins — exactly right for "lock down for this tenant". It's env-only (no PersistentConfig) so deploys are reproducible.
- `USER_PERMISSIONS_*` is the per-role grant layer (admin always passes). Use these for "show feature to admin, hide from users".
- `ENABLE_*` (PersistentConfig) is the legacy upstream layer — DB-overridable. For locked deploys, `ENABLE_PERSISTENT_CONFIG=False` makes env vars authoritative.
- Audit logging is middleware-based and only covers mutations. GET endpoints (including admin chat browsing) are NOT audited — combine `ENABLE_ADMIN_CHAT_ACCESS=False` with the audit middleware to get coverage of who-did-what for sensitive flows.
- The "no admin impersonation" finding is reassuring — confirmed no `login_as_user` or similar feature.

## Historical Context (from collab/)

- `collab/index.md` 26-03-2026 — `FEATURE_WEBPAGE_URL` and `FEATURE_REFERENCE_CHATS` introduced as additional InputMenu kill-switches.
- `collab/index.md` 26-03-2026 — Agent Proxy (`ENABLE_AGENT_PROXY`) is reverse-proxy at `/api/v1/agent/` for external sk- callers; SEPARATE from `AGENT_API_ENABLED` which is internal chat routing. Don't confuse them.
- `collab/index.md` 30-03-2026 — TOTP 2FA fully implemented (`ENABLE_2FA`, `REQUIRE_2FA`, `TWO_FA_GRACE_PERIOD_DAYS`).
- `collab/index.md` 31-03-2026 — DPIA features (`ENABLE_DATA_EXPORT`, `DATA_RETENTION_TTL_DAYS`, retention warning emails) shipped via PR #66.

## Open Questions

- The "Files" workspace tab user mentions: there is no separate `ENABLE_FILES`/`FEATURE_FILES` because the tab isn't registered in this fork's workspace layout. Verify this matches what you see in your build.
- Admin General tab Advanced Parameters has no global hide — would require code change. Is that acceptable, or do you want a small patch?
- Audit log retention/shipping: if you need long-term retention or SIEM forwarding, decide on rotation policy + shipper before go-live.
