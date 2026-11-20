# Dead-feature removal — owui-dead-features-fix1

The product owner's explicit exceptions override the read-only platform registry:
voice/audio, scheduled automations/calendar, skills, workspace/sandboxes,
transcription, notes, memories, folders, API keys, task switches, arena and webhooks
remain. Browser execution is removed despite its live registry classification.
Upstream implementations stay in place; `soev/product_surface.py` makes filtered
copies at the fork's mount boundary, preserving source routers and dependencies.
The security source inventory resolves those copies to their original handlers.
No backward compatibility routes or configuration aliases are added.

## Validation commands

Backend uses `/Users/lexlubbers/Code/soev/open-webui/.venv/bin/python`, with
`PYTHON_DOTENV_DISABLED=1 PYTHONPATH="$PWD/backend" DO_NOT_TRACK=true SCARF_NO_ANALYTICS=true`.
Each pytest invocation uses a new temporary SQLite database via the repository's
`backend/open_webui/test/conftest.py`; no Postgres database or role is created.

The shared targeted security command (S) is:

```sh
python -m pytest -c backend/pytest.ini \
  backend/open_webui/test/soev/test_dead_features.py \
  backend/open_webui/test/security/test_openapi_surface.py \
  backend/open_webui/test/security/test_writable_fields.py \
  backend/open_webui/test/security/attack/test_plane.py \
  backend/open_webui/test/security/attack/test_seeds.py \
  backend/open_webui/test/security/attack/test_crossuser.py \
  backend/open_webui/test/security/attack/test_soev_mode.py \
  backend/open_webui/test/security/test_route_coverage.py -q
```

OpenAPI is regenerated using `python scripts/security/export_openapi.py`, with
`PYTHON_DOTENV_DISABLED=1`, all six `DATABASE_{TYPE,USER,PASSWORD,HOST,PORT,NAME}` parts
empty, `DATABASE_URL=sqlite:///<temporary>/webui.db DATA_DIR=<temporary>` and
`STATIC_DIR=<temporary>/static VECTOR_DB=chroma CHROMA_HTTP_HOST= OFFLINE_MODE=true
WEBUI_SECRET_KEY=disposable-offline-openapi-export-key`. Temporary export data is
deleted on completion. No application lifespan or external services run.

The original `npm run check` baseline has 5,488 errors and 113 warnings. Touched
frontend files must add no diagnostics; comparisons ignore shifted line numbers.
Whole-file Ruff F checks on upstream `main.py` and `config.py` found 27 pre-existing
issues; new Python and security changes are checked separately and those upstream
files are compared to their Git baseline. Upstream files are never reformatted.

## Retrieval, local RAG and OWUI web loading

Unmounted four POST operations: `/api/v1/retrieval/process/url`, `/process/web`,
`/process/youtube`, `/process/web/search`. The two platform file-ingestion routes
stay. Webpage attachment is disabled through the existing feature gate; platform
web search stays available. Deleted the fork RAG-filter metadata/config exposure
and default. Removed unused chunk/split/tokenizer, YouTube, OWUI web-loader and
search-engine defaults. Extraction deployment controls and file limits stay.

Retained dead RAG defaults with reads in retained upstream handlers:
`rag.bypass_embedding_and_retrieval`, `rag.top_k`, `rag.top_k_reranker`,
`rag.relevance_threshold`, `rag.hybrid_bm25_weight`, `rag.enable_hybrid_search`,
`rag.enable_hybrid_search_enriched_texts`, `rag.full_context`, `rag.template`,
`rag.pdf_extract_images`, `rag.pdf_loader_mode`. These reads exist in
`retrieval/utils.py`, `utils/middleware.py`, `tools/builtin.py` and loader config;
the v2 dispatcher bypasses the local turn paths. Removing them safely would
require changing retained upstream code. Embedding-engine settings were already
absent before this run. `web.search.enable` and confirmation settings belong to
the retained platform gateway UI.

Inventory: 376 → 372 operations; 139 → 135 writable-body operations;
1,846 → 1,839 writable strings. Only declarations for removed operations were
removed; coverage and authorization assertions remain in force.

Validation: S passed (395 tests, 20 dependency warnings); regenerated OpenAPI;
Ruff F passed on new/helper/security files, with no new upstream Ruff findings.
No frontend source changed in this category.

## Direct connections and inference proxies

Unmounted all 21 remaining `/ollama/*` operations and both POST embedding aliases
(`/api/embeddings`, `/api/v1/embeddings`). OpenAI proxies and direct-connection UI
were already absent. Upstream router/embedding implementations remain; the fork's
mount code filters them. Removed the fork-only Ollama sweep seeder and its tests;
kept all three-actor upstream proxy authorization assertions using independent
fixtures. Removed only stale inventory controls, parameters, and embedding waivers.

Retained `ollama.enable`, `ollama.base_urls`, `ollama.api_configs`: retained
`utils/models.py` can still read them on the upstream catalog path when v2 is off.
All OpenAI/direct defaults were already absent. No new UI flags or defaults added.

Inventory: 372 → 349 operations; 135 → 114 writable-body operations;
1,839 → 1,152 writable strings. Validation: S plus
`backend/open_webui/test/security/attack/test_ollama_refusals.py` passed
(400 tests, 20 dependency warnings). No frontend source changed.

## Image generation

Unmounted POST `/api/v1/tasks/image_prompt/completions`; OWUI image engine routes
and defaults were already removed. The public generation availability is always
false, independently of platform image-input capability. Removed the image prompt
default, composer pin entry, saved generation restoration and outgoing generation
preference. Image uploads/vision and platform agent image behavior are unchanged.
The retained upstream components still have internal generation properties, now
disabled by the product gate. No image-generation config default remains.

Inventory: 349 → 348 operations; 114 → 113 writable-body operations;
1,152 → 1,112 writable strings. Validation: S plus
`test/soev/test_feature_capabilities.py` and `test/soev/test_task_turns.py`
passed (512 tests, 20 dependency warnings). Upstream task refusal assertions remain
covered independently of the retired product inventory.

Frontend commands:

```sh
node node_modules/vitest/vitest.mjs run src/lib/utils/deadFeatures.test.ts src/lib/utils/composerPreferences.test.ts src/lib/components/chat/MessageInput/inputItems.test.ts --exclude '**/nothing'
node node_modules/eslint/bin/eslint.js src/lib/utils/composerPreferences.ts src/lib/utils/deadFeatures.test.ts src/lib/components/chat/MessageInput/inputItems.ts src/lib/components/chat/MessageInput/inputItems.test.ts
node node_modules/prettier/bin/prettier.cjs --check src/lib/utils/composerPreferences.ts src/lib/utils/deadFeatures.test.ts src/lib/components/chat/MessageInput/inputItems.ts src/lib/components/chat/MessageInput/inputItems.test.ts
npm run check
```

Vitest: 18 passed. ESLint and Prettier: passed. Check: inherited tree errors remain;
touched files have zero errors/warnings in both baseline and current logs.
`--exclude '**/nothing'` replaces Vite's blanket `**/.worktrees/**` test exclusion,
which would otherwise hide every explicitly selected test in this worktree.

## Connector settings

Local connector sync defaults/routes (Confluence, Topdesk, Google Drive and
OneDrive local sync loops, daemon/document-pipeline wiring) were already absent.
Removed the remaining personal OneDrive config defaults, environment parsing and
`/api/config` exposure (`onedrive.enable_personal`, `onedrive.client_id_personal`).
Platform cloud-sync schedules/connections, business picker, live documents and
mail remain; these are not the obsolete OWUI connector sync settings.
No dead connector default remains. No routes added or removed; OpenAPI unchanged.

Validation: `python -m pytest -c backend/pytest.ini
backend/open_webui/test/soev/test_dead_features.py
backend/open_webui/test/soev/test_feature_capabilities.py
backend/open_webui/test/soev/test_live_documents.py -q` passed (48 tests,
16 dependency warnings). No frontend source changed.

## Local orchestration and task settings

Unmounted POST `/api/v1/chats/{id}/compact`, POST
`/api/v1/chats/{id}/messages/{message_id}/resolve`, and GET `/api/v1/tasks/config`.
Subagent/chat configuration writers were already removed. Existing compaction and
approval UI gates are now false without reading dead settings. Removed unused
`task.tools.prompt_template` default. Platform subagent progress presentation stays:
it displays platform events, independently of OWUI's local orchestration.

Retained defaults because retained upstream completion/turn paths still read them
when not routed to v2: all seven `subagents.*` keys; all six
`chat.context_compaction.*` keys; `chat.tool_permissions.enable`;
`task.model.default`, `task.model.external`, `task.model.params`;
`task.title.prompt_template`, `task.tags.prompt_template`,
`task.follow_up.prompt_template`, `task.query.prompt_template`,
`task.autocomplete.prompt_template`. The v2 branches bypass these reads, as
`test_agent_compaction.py` and `test_task_turns.py` verify. Voice task settings and
all task enable switches remain under the explicit exceptions.

Inventory: 348 → 345 operations; 113 → 111 writable-body operations;
1,112 → 1,108 writable strings. Validation: S plus
`backend/open_webui/test/soev/test_agent_compaction.py` and
`backend/open_webui/test/soev/test_task_turns.py` passed (498 tests,
20 dependency warnings). No frontend source changed.

## Local retention and archival workers

Deleted the fork's daily archival and TTL scheduler functions, their startup tasks,
the complete `services/retention/` implementation, unused retention config forms
and eight defaults/environment parses. Removed local retention TTL exposure from
`/api/config` and the empty archive-router mount. Archive management routes were
already removed, so the OpenAPI operation count is unchanged.

Kept `admin.enable_user_archival` and `admin.default_archive_retention_days`:
`services/deletion/offboarding_store.py` and `models/user_archives.py` still read
them. The shared offboarding/archive helper chain remains as a dependency of the
retained deletion service and its security boundaries. Removing it would require
expanding this change into offboarding; its v2 console refusal remains tested.
Platform retention/deletion mirroring is retained. Chat archive/unarchive is a
personal sidebar organization choice, not the retired account archival policy,
and is retained (it has no dead registry setting).

Updated the legacy content-access inventory for the deleted service, preserving
its exact source equality and unguarded-writer assertions. Validation:

```sh
python -m pytest -c backend/pytest.ini backend/open_webui/test/soev/test_retention_mirror.py backend/open_webui/test/soev/test_product_config.py backend/open_webui/test/soev/test_thread_content_inventory.py backend/open_webui/test/soev/test_policy_jobs.py backend/open_webui/test/security/test_openapi_surface.py -q
python -m pytest -c backend/pytest.ini backend/open_webui/test/soev/test_dead_features.py -q
```

The first command passed 95 tests; 38 existing parameter cases require the
unconfigured Postgres test target and were skipped by their existing fixtures.
No skip condition or assertion was loosened. SQLite cases and the non-database
worker absence check run. No frontend source changed.

## User-authored tools, functions and tool servers

Unmounted every remaining tools/functions operation, POST
`/api/chat/actions/{action_id}`, and GET tool-OAuth authorize/callback operations:
21 operations total (exact tombstones are in `security/attack-surface.toml`).
Upstream handlers remain. Deleted fork tool availability environment flags,
OAuth-client timeout default and obsolete sweep seeds/controls/waivers.
Tool-server, tool-authoring and OWUI built-in-tool gates are permanently false;
voice, skills and terminals retain their independent gates. Kept the existing
upstream function/action authorization assertions with independent test fixtures.

Chat initialization now checks the tools gate before fetching, allowing kept
composer defaults to initialize after the tool route is removed. The image menu
also checks availability even for old saved state or URL selection. Retained
`tool_server.connections`: boot migration and upstream helpers still read it.
No local tool data is offered by the v2 runtime.

Inventory: 345 → 324 operations; 111 → 107 writable-body operations;
1,108 → 967 writable strings; 36 → 35 query-string operations, 78 → 77 strings.
Validation: S plus `backend/open_webui/test/util/test_features.py`: 411 passed,
20 dependency warnings. Ruff: zero new findings on all changed Python files.

Frontend: targeted Vitest for `features.test.ts`, `composerPreferences.test.ts`,
`deadFeatures.test.ts`, `inputItems.test.ts`: 51 passed. ESLint is clean on the
fork utility files. The two minimally touched upstream Svelte files retain their
49 pre-existing ESLint findings, with zero additions (compared by file, rule,
severity and message, ignoring shifted lines). Prettier is clean on all touched
frontend files. No new lint suppression was added.

The first type-check baseline failed to load Vite configuration, suppressing four
warnings on unchanged self-closing tags. A supplemental baseline temporarily used
the untouched `5953cb095` versions of Chat.svelte and InputMenu.svelte under the
working config, then restored the edits in a finally block. Against that baseline,
`npm run check` has 263 diagnostics in these files both before and after: zero new
errors/warnings. Both baseline logs are retained in `/tmp` for review.

## Local authentication, LDAP, password recovery and OWUI MFA

Removed 13 operations: all six `/api/v1/auths/2fa/*` operations and local
signin/signup/LDAP/password-change/recovery/validation. Deleted the fork's TOTP
router, password-recovery handlers/forms, MFA frontend components, reset-password
page, MFA/recovery API clients and MFA enrollment/grace UI. Upstream password,
LDAP and signup handlers remain unmounted. Their UI availability gates are false;
dead MFA/recovery settings are no longer read or published by `/api/config`.
Removed LDAP, local form, local admin-detail, MFA and recovery defaults where no
retained route reads them. OIDC login, session/profile/timezone reads and writes,
API keys, invitations and backchannel logout remain.

Retained dead defaults used by the kept OAuth manager/provisioning path:
non-OIDC provider registration keys; OAuth role/group/tenant policy keys;
`ui.default_user_role` and `ui.default_group_id`. These are source-level reads,
including registration at startup and callback admission. They are not safe to
remove merely because Keycloak is the intended provider. MFA/reset token models
and their helper dependencies remain because console offboarding erases those
existing database rows, and preserved upstream handlers import some helpers.
Purpose-token refusal and revocation remain enforced on kept sessions/API keys.

Retired issuer tests now exercise the actual app route set, require exact 404,
no account/secret writes, no session issuance and no cookies. Tests still cover
revoked token/user markers, valid codes, full sessions, wrong/right passwords,
and both platform and standalone settings. Updated the public-route auth audit
by removing stale declarations; its unguarded-route assertion is unchanged.
Derivation floors retain their original margins, subtracting exact retired
body/query route and field counts; query floors are not exempted.

Inventory: 324 → 311 operations; 107 → 97 writable-body operations;
967 → 948 writable strings; query counts remain 35 / 77.
Validation: S plus `attack/test_platform_session_issuers.py`,
`soev/test_session_issuers.py`, `security/test_totp_password.py`,
`soev/test_session_boundary.py`, `soev/test_local_password_boundary.py`,
`authorization/test_routes.py` (all under `backend/open_webui/test/`):
465 passed, 20 dependency warnings. Ruff: zero new findings.

Frontend: `node node_modules/vitest/vitest.mjs run
src/lib/utils/authPage.test.ts src/lib/apis/auths/index.test.ts
--exclude '**/nothing'`: 14 passed. Prettier passed on auth page, app layout,
account settings and auth API client. Targeted ESLint: 12 inherited findings,
zero new. `npm run check`: touched files 124 → 120 diagnostics against the valid
baseline, zero additions.

## Browser code execution

Disabled Pyodide execution through the existing fork feature gates, including
stale selected tools and historical file-navigation tabs. Code blocks default to
no execution while config loads. `/api/config` offers execution/interpreter only
for an explicitly configured Jupyter engine, and never offers Pyodide persistence.
Both legacy-tag handling and the upstream built-in execute tool now gate browser
RPC production; stale execute RPCs are explicitly refused by the frontend before
creating a worker. Added the refusal's Dutch and English translations.

No route removed: `/api/v1/utils/code/execute` is an independent authenticated
Jupyter endpoint and remains. No code/Jupyter default is classified dead except
`code_interpreter.prompt_template`, retained because preserved upstream prompt
injection still reads it for Jupyter/legacy turns. Browser execution is a product
removal despite its registry's live classification.

Pyodide files, asset serving and isolation code also support the kept code
editor's Python text formatter. These remain for formatting; user-code execution
and the interpreter filesystem UI are not offered. No isolation assertion or
sandbox test was removed or weakened.

Validation: `python -m pytest -c backend/pytest.ini
backend/open_webui/test/soev/test_dead_features.py
backend/open_webui/test/util/test_features.py
backend/open_webui/test/security/test_openapi_surface.py -q`: 32 passed,
20 dependency warnings. Tests prove a stored Pyodide enable flag cannot offer
execution, no browser RPC occurs, and configured Jupyter execution still works.
OpenAPI remains identical. Ruff has zero new findings in changed Python files.

Targeted Vitest (`features.test.ts`, `deadFeatures.test.ts`, `inputItems.test.ts`):
44 passed. Prettier passed on touched frontend files and both locale files.
ESLint: no new findings against upstream baseline; fork utility files clean.
Type check: touched files 171 → 170 diagnostics, zero additions.
The underlying i18next parser ran with a temporary config scoped to the root
layout and output under `/tmp/owui-dead-i18n/`; both catalogs contain the new key.
This avoids rewriting upstream locale catalogs with the broad npm script.

## Fork-only data warnings and obsolete setting reads

Deleted the fork's data-warning acceptance route, log writer/read helpers, API
client, confirmation flow, assistant-editor component and metadata controls.
Removed `features.enable_data_warnings` default/environment parsing, public
exposure and frontend type. Legacy ORM rows remain solely so console offboarding
can erase them. Removed special merging of obsolete warning metadata; general
model metadata behavior stays. The removed test covered insertion by this retired
feature; no isolation or authorization assertion was removed.

Also replaced the stale, unused requests for legacy positive/negative feedback
label keys in `/api/config` with the live reasons/tags keys that the response
already reads. No evaluation-model or arena route/behavior was changed.

Removed POST `/api/v1/data-warnings/accept`: 311 → 310 operations;
97 → 96 writable-body operations; 948 → 944 writable strings. No dead default
remains for data warnings. Inventory declarations were removed only for that route.
Validation: S plus `backend/open_webui/test/security/test_plane_findings.py` and
`backend/open_webui/test/soev/test_model_catalog.py`: 444 passed,
20 dependency warnings. Ruff: zero new findings.

Targeted Vitest (`composerPreferences.test.ts`, `deadFeatures.test.ts`,
`assistantCapabilities.test.ts`): 25 passed. Prettier passed on touched files.
Targeted ESLint: 104 inherited findings, zero additions. Type check: touched
files 460 → 452 diagnostics against the valid baseline, zero additions.
