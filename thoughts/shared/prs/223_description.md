Implements all three phases of `thoughts/shared/plans/2026-07-03-weaviate-mt-legacy-cleanup.md` — the end-state cleanup the original MT plan prescribed, now that every tenant runs native multi-tenancy and the dual-read fallback is disabled fleet-wide (soev-gitops #48). One commit per phase.

## Phase 1 — Remove the legacy connector and fallback shim

- **Factory**: `VectorType.WEAVIATE` unconditionally returns the MT client; `ENABLE_WEAVIATE_MULTITENANCY_MODE` and `WEAVIATE_MT_LEGACY_FALLBACK` deleted from `config.py` (plain import-time env reads, no PersistentConfig migration needed).
- **MT connector** (`weaviate_multitenancy.py`): dual-read shim removed — legacy class lookup (`_legacy_sanitize_collection_name`/`_legacy_class_exists`), the empty-result → legacy fallback in `search`/`query`/`get` (+ now-unused `_result_is_empty`), the legacy OR-branch in `has_collection`, and the anti-resurrection double deletes in `delete`/`delete_collection`. The three shared helpers (`_make_json_serializable`, `_sanitize_property_name`, `_sanitize_metadata_keys`) moved in verbatim from the deleted legacy module (grep-verified: no external consumers).
- **`dbs/weaviate.py` deleted.** Upstream-merge note: this file originates upstream (PR #14747) but was already heavily fork-modified; future upstream changes to it surface as delete/modify conflicts — resolve by re-deleting (the fork's Weaviate support is the MT connector).
- **Tests**: `test_weaviate_factory_selection.py` deleted (tested only the dual-connector switch); `test_weaviate_index_policy.py` rewritten MT-only; `test_weaviate_mt_mapping.py` untouched.

Behavior-neutral for any tenant already running fallback-off: every deleted path was already disabled at runtime.

## Phase 2 — Port `source_url` into the MT schema

`_mt_properties()` had drifted from the legacy property list: PR #195 (citation links) added `source_url` to the legacy connector only. With auto-schema off, MT tenants were **silently dropping `source_url` at insert** — and deleting the legacy module would have removed the only declaration of it.

- `source_url` (TEXT) added to `_mt_properties()`, which is now the canonical list.
- Legacy `_ensure_source_url_property` ported to collection level: pre-existing collections get the property added idempotently on first `_ensure_collection` (schema is collection-level and tenant-agnostic, so one call covers all tenants; best-effort with a per-process cache, never blocks ingest).
- New drift-guard test `test_weaviate_mt_schema.py` pins `source_url` + the core provenance fields.

Existing cloud-sync KBs still need a re-sync to populate the value (known follow-up of the citation-links release).

## Phase 3 — Chart cleanup (1.0.6 → 1.0.7)

- `enableWeaviateMultitenancyMode` / `weaviateMtLegacyFallback` removed from values + configmap.
- `ENABLE_WEAVIATE_MULTITENANCY_MODE: "true"` stays **hardcoded** in the configmap for one release cycle: an old image rendered with the new chart would otherwise default to the legacy connector and read empty/deleted per-class data ("my KB is empty"). Dropping `WEAVIATE_MT_LEGACY_FALLBACK` is safe for old images — their default `true` is a no-op read against legacy classes.
- Stray per-tenant gitops values keep rendering (no `values.schema.json`; verified with `--set openWebui.config.weaviateMtLegacyFallback=true`).

## Testing

- `pytest open_webui/test/util/ -k weaviate`: 41 passed (MT mapping, MT-only index policy, new schema guard).
- Repo-wide grep for both flags + all legacy symbols returns nothing in `backend/`; only the hardcoded configmap line remains in `helm/`.
- Factory imports cleanly with default `VECTOR_DB`; pylint score improved (7.26 → 7.28), no new warnings in touched files.
- `helm lint` passes; template renders the hardcoded enable line and zero fallback references.
- Full `open_webui/test/util/` sweep: 11 failures, byte-identical to `dev` HEAD in a clean worktree (redis sentinel, internal retrieval, ingest attachments, skill files — env-dependent, none weaviate-related). Zero new failures.
- Manual staging verification (per plan, after image+chart roll): pod env shows the hardcoded enable and no fallback var; KB upload/query/delete, web search, memory; Weaviate schema shows `source_url` on `Knowledge`/`File` after first ingest; cloud-sync re-sync → citation links to the original page; no `could not find class C<uuid>` errors; agent retrieval unchanged.

## Rollout notes

- **Ordering**: merge soev-gitops #48 (fallback off fleet-wide) and let it soak before this lands on a deployed track — that PR is the behavior change; this one deletes already-dead code.
- **Rollback**: pin the previous image tag in the tenant HelmRelease; the per-tenant flag keys still exist in gitops until the follow-up cleanup, so an old image honors them.
- **Follow-ups (separate PRs)**: gitops removal of the 4 per-tenant MT keys + scaffold keys; drop the hardcoded configmap line next chart cycle; agent-side mirror shim removal in genai-utils/soev-agents; optional cluster-ops deletion of residual legacy classes.
