# Weaviate MT Legacy-Fallback Code Cleanup Implementation Plan

## Overview

Remove the pre-MT (legacy per-class Weaviate) code paths from open-webui now that every tenant runs native multi-tenancy with the dual-read fallback disabled fleet-wide (soev-gitops PR #48). This is the end-state cleanup the original MT plan prescribed: "later, delete the legacy `weaviate.py` path + fallback flag" (`thoughts/shared/plans/2026-06-03-weaviate-native-multitenancy-refactor.md:321`).

**Target branch: `dev`** (fork flow: dev → test → main; soaks on the staging tenant, which is the only dev-track tenant with OWUI Weaviate).

## Current State Analysis

Full inventory: `../../../thoughts/shared/research/2026-07-03-weaviate-mt-fallback-cleanup.md` (monorepo thoughts). Verified against `origin/dev` @ `cc687c9d7` — line numbers below are dev's.

- **Mode switch**: `backend/open_webui/retrieval/vector/factory.py:80-88` branches on `ENABLE_WEAVIATE_MULTITENANCY_MODE` (`config.py:2883`, default `false`) between the legacy connector (`dbs/weaviate.py`) and the MT connector (`dbs/weaviate_multitenancy.py`). Nothing else in `backend/` branches on either flag.
- **Fallback shim**: entirely inside `weaviate_multitenancy.py`, gated on `WEAVIATE_MT_LEGACY_FALLBACK` (`config.py:2886`, default `true`) → `self.legacy_fallback` (`:151`): `_legacy_sanitize_collection_name` (`:90-110`), `_legacy_class_exists` (`:208-218`), dual-read in `has_collection`/`search`/`query`/`get`, anti-resurrection double deletes in `delete`/`delete_collection`.
- **Helper coupling**: `weaviate_multitenancy.py:34-38` imports `_make_json_serializable`, `_sanitize_property_name` (noqa re-export, no external consumers — verified), `_sanitize_metadata_keys` from `weaviate.py`.
- **Schema drift (must fix, not just delete)**: `_mt_properties()` (`weaviate_multitenancy.py:117-144`) claims to be a copy of the legacy property list but drifted — legacy `weaviate.py` gained `source_url` (PR #195 citation links, property at `:179` + `_ensure_source_url_property` shim at `:211-236`) and the MT copy did not. With auto-schema-off/fixed-schema behavior (see `factoid_owui_weaviate_fixed_schema_drops_metadata`), MT tenants **currently drop `source_url` at insert**. Deleting `weaviate.py` without porting this would permanently remove the only declaration of a property the citation feature needs.
- **Chart**: `helm/open-webui-tenant/values.yaml:222-230` defines `enableWeaviateMultitenancyMode: "false"` / `weaviateMtLegacyFallback: "true"`; `templates/open-webui/configmap.yaml:69-70` renders both unconditionally. Chart default is still legacy mode — only per-tenant gitops overrides turn MT on.
- **Tests**: `test_weaviate_factory_selection.py` exists solely for the dual-connector switch; `test_weaviate_index_policy.py` covers both connectors' `_build_vector_config`; `test_weaviate_mt_mapping.py` is MT-only (untouched).
- **Upstream note**: `weaviate.py` originates upstream (upstream PR #14747, "feat: add support for Weaviate vector database"), but is already heavily fork-modified (C-prefix sanitization, `source_url`, OneDrive props, BQ policy). Deleting it converts future upstream-merge conflicts on this file into delete/modify conflicts — resolve by re-deleting (the fork's Weaviate support is the MT connector).

### Prerequisites (already done)
- All tenants migrated to native MT (previder 2026-06-05, intermax 2026-06-17, nebul 2026-06-21).
- `weaviateMtLegacyFallback` flipped to false fleet-wide, both OWUI and agent side: **soev-gitops PR #48** (merge + short soak before this lands on a deployed track).

## Desired End State

- One Weaviate connector: `weaviate_multitenancy.py`, MT-only, no fallback code, self-contained helpers, schema including `source_url`.
- `dbs/weaviate.py` deleted; `ENABLE_WEAVIATE_MULTITENANCY_MODE` and `WEAVIATE_MT_LEGACY_FALLBACK` gone from `config.py`; factory's `VectorType.WEAVIATE` case unconditionally returns the MT client.
- Chart no longer exposes the two values; ConfigMap keeps a **hardcoded** `ENABLE_WEAVIATE_MULTITENANCY_MODE: "true"` for one release cycle (old-image skew guard — see Phase 3 reasoning).
- Verify: `grep -rn "WEAVIATE_MT_LEGACY_FALLBACK\|ENABLE_WEAVIATE_MULTITENANCY_MODE" backend/ helm/` returns only the hardcoded configmap line; pytest weaviate suites green; staging tenant works end-to-end on the dev image.

### Key Discoveries
- The flags are plain import-time env reads, not PersistentConfig — no DB/persistent_config migration needed (`config.py:2883,2886`).
- No consumer outside `config.py`/`factory.py`/`weaviate_multitenancy.py` (grep-verified on dev) — no router/util changes.
- Old-image + new-chart skew is the one dangerous combination: without the env var, an old image defaults `ENABLE_WEAVIATE_MULTITENANCY_MODE=false` → silently flips to the legacy connector → reads empty/deleted legacy classes ("my KB is empty"). Hence the hardcoded configmap line for a deprecation cycle.
- Dropping `WEAVIATE_MT_LEGACY_FALLBACK` from the chart is safe for old images: their default is `true`, which is a no-op read against legacy classes (same behavior as pre-#48).

## What We're NOT Doing

- **Not removing `ENABLE_WEAVIATE_MULTITENANCY_MODE` from the chart configmap yet** — the hardcoded `"true"` line stays one release cycle as skew protection; a later chart bump removes it once all tracks run images ≥ this release.
- **Not touching the gitops per-tenant keys** — the now-unused `enableWeaviateMultitenancyMode`/`weaviateMtLegacyFallback` (OWUI) and `weaviateMultitenancyEnabled`/`weaviateMtLegacyFallback` (agent) values are harmless once unrendered; a follow-up soev-gitops PR removes all 4 keys per tenant + the scaffold templates after the fleet is on cleanup images.
- **Not cleaning the genai-utils/soev-agents mirror** — the agent-side dual-read shim (`weaviate_mt_legacy_fallback`) is a separate repo/release; same shape, done separately. The `_weaviate_naming.py` mapping copy stays (MT-permanent, parity-critical).
- **Not deleting residual legacy Weaviate classes in clusters** — the skipped `C<63/64-hex>` HashBased classes (and any empty leftovers) are dead weight once fallback is off; physical deletion is a cluster-ops task (migrate.py `--delete-after-verify` or manual schema deletes), not code.
- **Not touching** `_weaviate_mt_mapping.py`, `ENABLE_WEAVIATE_BQ_QUANTIZATION`, the Weaviate connection vars, the Milvus/Qdrant MT flags, or the unused `WEAVIATE_WEB_SEARCH_TTL_MINUTES` (separate trivial cleanup if wanted).
- **Not backfilling `source_url` on existing MT chunks** — Phase 2 fixes the schema so new inserts keep it; existing cloud-sync KBs need a re-sync to populate it (already a known follow-up of the citation-links release).

## Implementation Approach

Three phases, one PR to `dev`. Phase 1 removes the dual-mode machinery, Phase 2 fixes the schema drift the deletion would otherwise bake in, Phase 3 cleans the chart. Phases 1+2 are image-side, Phase 3 chart-side; they ship together but are separable commits for review.

Branch: `chore/weaviate-mt-legacy-cleanup` off `origin/dev`.

## Phase 1: Remove the legacy connector and fallback shim

### Overview
Make the MT connector the only Weaviate connector: unconditional factory case, no fallback reads/writes, no flags, helpers relocated, legacy module deleted.

### Changes Required

#### 1. Factory — unconditional MT
**File**: `backend/open_webui/retrieval/vector/factory.py`
- Remove `ENABLE_WEAVIATE_MULTITENANCY_MODE` from the `open_webui.config` import block (`:7`).
- Collapse the `case VectorType.WEAVIATE:` branch (`:80-88`) to a single lazy import of `weaviate_multitenancy.WeaviateClient` with a one-line comment that the legacy per-class connector was removed after the fleet-wide MT migration (2026-07).

#### 2. Config — drop both flags
**File**: `backend/open_webui/config.py`
- Delete the `ENABLE_WEAVIATE_MULTITENANCY_MODE` (`:2883`) and `WEAVIATE_MT_LEGACY_FALLBACK` (`:2886`) definitions and their comments. Keep `ENABLE_WEAVIATE_BQ_QUANTIZATION` and all `WEAVIATE_*` connection vars.

#### 3. MT connector — remove shim, absorb helpers
**File**: `backend/open_webui/retrieval/vector/dbs/weaviate_multitenancy.py`
- Module docstring (`:1-30`): drop the migration-window/dual-read paragraphs; state it is the sole Weaviate connector (5 MT collections + `Knowledge_bases` meta).
- Imports: remove `ENABLE_WEAVIATE_MULTITENANCY_MODE` and `WEAVIATE_MT_LEGACY_FALLBACK` (`:57-59`); remove the `from ...dbs.weaviate import` block (`:33-38`).
- Move `_make_json_serializable`, `_sanitize_property_name`, `_sanitize_metadata_keys` verbatim from `weaviate.py` into this module (private, above the class; no external consumers exist).
- Delete `_legacy_sanitize_collection_name` (`:90-110`) and `_legacy_class_exists` (`:208-218`).
- `__init__`: drop `self.legacy_fallback` (`:150-151`).
- `has_collection` (`:368-375`): tenant/meta existence only — drop the legacy-class OR-branch.
- `search` (`:419-438`), `query` (`:440-452`), `get` (`:454-466`): drop the empty-result → legacy fallback branch; `_result_is_empty` (`:343-351`) becomes unused — delete it.
- `delete` (`:492-508`): drop the legacy-class mirror delete + its warning log.
- `delete_collection` (`:510-534`): drop the legacy-class drop + warning logs; keep tenant/shard removal and meta-collection handling.
- Keep `reset` (drops all collections — mode-independent) and everything else untouched.

#### 4. Delete the legacy module
**File**: `backend/open_webui/retrieval/vector/dbs/weaviate.py` — delete. (Upstream-merge note: future upstream changes to this file will surface as delete/modify conflicts; resolve by re-deleting.)

#### 5. Tests
- **Delete** `backend/open_webui/test/util/test_weaviate_factory_selection.py` (tested only the dual-connector switch).
- **Rewrite** `backend/open_webui/test/util/test_weaviate_index_policy.py`: drop the legacy-connector half (imports from `dbs.weaviate`, prefix-based assertions, `_FLAT_INDEX_PREFIXES`); keep the MT half (`_MT_FLAT_COLLECTIONS`, exact-name BQ policy) as the whole file.
- `test_weaviate_mt_mapping.py`: unchanged.

### Success Criteria

#### Automated Verification:
- [x] `cd backend && python -m pytest open_webui/test/util/test_weaviate_mt_mapping.py open_webui/test/util/test_weaviate_index_policy.py -v` passes
- [x] `grep -rn "WEAVIATE_MT_LEGACY_FALLBACK\|ENABLE_WEAVIATE_MULTITENANCY_MODE\|dbs\.weaviate import\|dbs/weaviate\.py\|legacy_fallback\|_legacy_class_exists\|_legacy_sanitize" backend/` returns nothing
- [x] `python -c "from open_webui.retrieval.vector.factory import Vector"` imports cleanly (with `VECTOR_DB` unset/chroma default)
- [x] `npm run lint:backend` passes (no new warnings in touched files; score 7.26 → 7.28; also dropped the now-unused `KNOWLEDGE`/`HASH_BASED` mapping imports)

#### Manual Verification:
- [ ] None at this phase (covered by Phase 2/3 staging soak)

---

## Phase 2: Port `source_url` into the MT schema

### Overview
Fix the `_mt_properties()` drift so the citation-links provenance key survives insert on MT collections — the legacy connector being deleted was the only place declaring it.

### Changes Required

#### 1. Property declaration
**File**: `backend/open_webui/retrieval/vector/dbs/weaviate_multitenancy.py`
- Add to `_mt_properties()` (`:117-144`), alongside the other TEXT provenance fields:
```python
        # Cloud-sync provenance: original page/document URL for citations (PR #195).
        weaviate.classes.config.Property(name='source_url', data_type=weaviate.classes.config.DataType.TEXT),
```
- Update the function's leading comment: it is no longer "copied from legacy" — it IS the canonical list now.

#### 2. Back-fill shim for existing MT collections
The 5 MT collections + meta already exist on every tenant and predate the property. Port the legacy `_ensure_source_url_property` pattern (`weaviate.py:211-236`) to collection level:
- Instance cache `self._source_url_ensured: set[str]`.
- In `_ensure_collection` (after the exists-check), for a pre-existing collection: if `source_url` not in the collection's properties, `collection.config.add_property(...)` idempotently (schema is collection-level, tenant-agnostic — one call covers all tenants). Swallow/log failures the same way the legacy shim did (best-effort; never block ingest).

#### 3. Test
- Add to `test_weaviate_index_policy.py` (or a small new `test_weaviate_mt_schema.py`): assert `_mt_properties()` includes a `source_url` TEXT property (guards against the next drift).

### Success Criteria

#### Automated Verification:
- [x] `cd backend && python -m pytest open_webui/test/util/ -k weaviate -v` passes (41 tests; drift guard added as new `test_weaviate_mt_schema.py`)
- [x] `grep -n "source_url" backend/open_webui/retrieval/vector/dbs/weaviate_multitenancy.py` shows property + ensure logic

#### Manual Verification (staging, dev track, after image rolls):
- [ ] `curl -s http://staging-weaviate:8080/v1/schema` (port-forward) shows `source_url` on `Knowledge` and `File` after first ingest touches them
- [ ] Re-sync a cloud-sync KB on staging; a citation in chat links to the original page (source_url present end-to-end)

**Implementation Note**: pause after Phase 2 lands on staging for the manual schema/citation check before promoting beyond the dev track.

---

## Phase 3: Helm chart cleanup

### Overview
Remove the two values from the chart; keep a hardcoded enable env for one release cycle so an old image rendered with the new chart cannot silently flip to a connector that no longer matches the data.

### Changes Required

#### 1. Values
**File**: `helm/open-webui-tenant/values.yaml`
- Delete `enableWeaviateMultitenancyMode` (`:222-226`) and `weaviateMtLegacyFallback` (`:227-230`) keys + comments. Keep `enableWeaviateBqQuantization`.

#### 2. ConfigMap
**File**: `helm/open-webui-tenant/templates/open-webui/configmap.yaml`
- Delete the `WEAVIATE_MT_LEGACY_FALLBACK` line (`:70`) — old images default to `true`, which is a harmless no-op now.
- Replace the `ENABLE_WEAVIATE_MULTITENANCY_MODE` line (`:69`) with a hardcoded value + removal marker:
```yaml
  # Hardcoded skew guard: images since the 2026-07 MT cleanup ignore this; older
  # images default to the (removed) legacy connector without it. Drop this line
  # once every track runs a cleanup-era image.
  ENABLE_WEAVIATE_MULTITENANCY_MODE: "true"
```

#### 3. Chart version
**File**: `helm/open-webui-tenant/Chart.yaml` — bump `version` (1.0.6 → 1.0.7 in-tree; release packaging renumbers per its own flow).

### Success Criteria

#### Automated Verification:
- [x] `helm lint helm/open-webui-tenant` passes
- [x] `helm template t helm/open-webui-tenant | grep -c WEAVIATE_MT_LEGACY_FALLBACK` returns 0
- [x] `helm template t helm/open-webui-tenant | grep 'ENABLE_WEAVIATE_MULTITENANCY_MODE: "true"'` returns the hardcoded line
- [x] `helm template t helm/open-webui-tenant --set openWebui.config.weaviateMtLegacyFallback=true` still renders (stray gitops values must not break rendering — Helm ignores unknown keys; no values.schema.json exists)

#### Manual Verification:
- [ ] Staging HelmRelease reconciles cleanly with the new chart + image; OWUI pod env shows `ENABLE_WEAVIATE_MULTITENANCY_MODE=true` and no `WEAVIATE_MT_LEGACY_FALLBACK`
- [ ] KB search / file upload+query / web search / memory all work on staging; no `could not find class C<uuid>` errors in logs (`stern -n staging 'open-webui' --since 15m`)

---

## Testing Strategy

### Unit Tests
- MT mapping suite (unchanged) — routing invariants.
- Index-policy suite (MT-only after Phase 1) — BQ flag behavior.
- New schema assertion — `source_url` present in `_mt_properties()`.

### Manual Testing Steps (staging, dev track)
1. After image+chart roll: upload a file to a KB, query it in chat; delete it; confirm no legacy-class errors in logs.
2. Web search in chat (exercises `WebSearch` tenant path + HashBased mapping).
3. Cloud-sync KB re-sync → citation click-through shows source_url link.
4. Agent path (`openwebuiDirect`): agent KB retrieval still works — the agent has fallback off since gitops PR #48, so behavior must be unchanged.

## Performance Considerations

Strictly positive: dual-read added a second Weaviate round-trip on empty results and double deletes; MT-only removes both. The `add_property` ensure-shim fires once per collection per process lifetime (cached set), negligible.

## Migration Notes

- **Ordering**: merge gitops PR #48 first and let it soak (it is behavior-changing: fallback off). This PR is then behavior-neutral for any tenant already running fallback-off — the deleted code paths were already disabled.
- **Rollback**: pin the previous image tag in the tenant HelmRelease (gitops). The per-tenant flag keys still exist in gitops until the follow-up cleanup, so an old image honors them.
- **Follow-ups (tracked, separate PRs)**:
  1. soev-gitops: remove the 4 per-tenant MT keys (all 12 tenants) + scaffold template keys once all tracks run cleanup-era images.
  2. open-webui chart: drop the hardcoded `ENABLE_WEAVIATE_MULTITENANCY_MODE` configmap line in the next chart cycle.
  3. genai-utils/soev-agents: remove the agent-side mirror shim + `weaviate_mt_legacy_fallback` config key.
  4. Cluster ops: delete residual legacy classes (HashBased leftovers + empties) per tenant — optional hygiene.

## References

- Research (full inventory): `thoughts/shared/research/2026-07-03-weaviate-mt-fallback-cleanup.md` (monorepo thoughts/)
- Original MT plan end-state: `thoughts/shared/plans/2026-06-03-weaviate-native-multitenancy-refactor.md:293,321,332` (this repo)
- Rollout completion + deferred items: `soev-gitops/thoughts/shared/commands/2026-06-05-weaviate-mt-rollout-COMPLETE.md:47-55`
- Fleet flag flip: soev-gitops PR #48 (`chore/weaviate-mt-legacy-fallback-off`)
- Fixed-schema metadata-drop behavior: memory factoid `owui-weaviate-fixed-schema-drops-metadata`; legacy `source_url` shim `weaviate.py:211-236` (dev)
