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
