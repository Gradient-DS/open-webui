# Restore Arbitrary External Provider Slugs at `/api/v1/integrations/ingest`

## Overview

Make `POST /api/v1/integrations/ingest` accept arbitrary `provider` slugs again (the pre-`cc24c435b` behaviour) so admin-UI-created external push providers — e.g. an `octobox` or `gradient` slug configured in **Admin → Integraties** — can ingest documents. The managed cloud-sync providers (`onedrive`, `google_drive`, `confluence`) keep their existing slug→prefix registry behaviour; only the validator semantics change.

## Current State Analysis

**Symptom**

`POST /api/v1/integrations/ingest` returns `400 Unknown provider slug: '<slug>'` for any `X-Acting-Provider`/service-account `provider` value other than `onedrive`, `google_drive`, or `confluence`. The Admin UI permits creating providers with arbitrary slugs (`backend/open_webui/routers/configs.py:830`), the knowledge router permits creating KBs with those slugs (`backend/open_webui/routers/knowledge.py:283-285` — `INTEGRATION_PROVIDERS.keys()` is unioned into `allowed_kb_types`), but the ingest endpoint rejects them.

**Regression source — commit `cc24c435b` ("feat: midway point", 2026-04-30)**

Two changes:

1. New file `backend/open_webui/services/sync/provider.py:37-54` — defines `PROVIDER_FILE_ID_PREFIXES = {'onedrive': 'onedrive-', 'google_drive': 'googledrive-', 'confluence': 'confluence-'}` and `file_id_prefix_for(slug)` that raises `ValueError` for any slug outside that dict.
2. `backend/open_webui/routers/integrations.py` — the three per-doc helpers (`_process_parsed_text_document` line 334, `_process_chunked_text_document` line 400, `_process_full_document` line 462) and the file-limit guard (line 649-650) changed from `file_id = f'{provider}-{doc.source_id}'` (slug-as-prefix, anything worked) to `file_id = f'{file_id_prefix_for(provider)}{doc.source_id}'`. A new upfront validation block at lines 589-595 calls `file_id_prefix_for(provider)` solely to raise 400 on unknown slugs before any DB writes.

**Why the registry exists** — Google Drive's worker writes stub File rows with prefix `googledrive-` while its provider slug is `google_drive` (slug ≠ prefix.rstrip('-')). Without the registry, the loader-worker callback reconstructed `file_id = f'google_drive-{item_id}'`, missed the `googledrive-…` stub, and inserted a duplicate row on every successful sync (the 2026-04-29 incident — see test docstring at `backend/open_webui/test/services/sync/test_provider_registry.py:60-66`). The registry is a slug→prefix override map for the three managed-sync providers, which are the only ones that have a worker class (and thus a `_FILE_ID_PREFIX` constant that needs to round-trip).

**What was lost** — External push providers (admin-UI-created via `INTEGRATION_PROVIDERS`, with no worker class, no stub creation) had no slug/prefix divergence to begin with. Their `file_id` was always `f'{slug}-{source_id}'` and the slug was the prefix verbatim. The new registry locks the validator to the three managed-sync slugs and excludes external push providers entirely.

**Latent test failure** — `backend/open_webui/test/util/test_integrations_loader_auth.py:351-370 (test_ingest_with_loader_bearer_unknown_provider_returns_403)` is currently failing with `assert 400 == 403`. This test was already wrong pre-`cc24c435b` (no 403 path existed for `LoaderPrincipal` + missing `INTEGRATION_PROVIDERS` entry — see `routers/integrations.py:577-584`'s `providers.get(provider) or {}` fallback comment), but the new 400 from `file_id_prefix_for` made it visible. We will retire this test as part of the fix.

### Key Discoveries

- `get_integration_provider` (`routers/integrations.py:77-97`) already raises 403 for unregistered slugs on the regular-user path — the upfront `file_id_prefix_for(provider)` validator at line 593 is fully redundant for that path and actively harmful for valid push-provider slugs.
- `LoaderPrincipal` callers (loader-worker bearer auth) intentionally bypass `INTEGRATION_PROVIDERS` lookup and use `providers.get(provider) or {}` (line 577-584). Their slugs are constrained de-facto by what the loader-worker is configured to send (the three managed-sync slugs).
- `KnowledgeBase.type` is the slug verbatim (`models/knowledge.py:399-409`'s `get_knowledge_bases_by_type` filter). For external push providers, `type=<slug>` and the KB is found via `meta.integration.source_id` (`routers/integrations.py:114-121`). No registry lookup required.
- `delete_document` (`routers/integrations.py:822`) still uses `file_id = f'{provider}-{document_source_id}'` — never updated to `file_id_prefix_for`. Pre-existing latent bug for `google_drive` only (file_ids are stored as `googledrive-…` but lookup uses `google_drive-…`). Out of scope for this fix.
- The registry in `services/sync/provider.py` is referenced by exactly two callers: `routers/integrations.py` (4 callsites) and `test/services/sync/test_provider_registry.py` (the registry guards). No other files import `file_id_prefix_for`.

## Desired End State

After this plan:

1. `POST /api/v1/integrations/ingest` with a service-account auth carrying `provider=<custom-slug>` (any slug registered in `INTEGRATION_PROVIDERS`) returns HTTP 200 and ingests documents — verified by the Octobox curl from `thoughts/shared/research/2026-03-06-octobox-integratie-email.md` (or any equivalent custom-slug push).
2. The managed sync providers (`onedrive`, `google_drive`, `confluence`) continue to round-trip `file_id`s correctly between worker stubs and ingest reconstruction — verified by the existing `test_round_trip_stub_vs_ingest_file_id` test passing unchanged.
3. `file_id_prefix_for(slug)` is a total function: returns `PROVIDER_FILE_ID_PREFIXES[slug]` if the slug is a managed sync provider, else `f'{slug}-'`. Its docstring documents the new fall-through semantics.
4. The redundant upfront 400-validation block in `routers/integrations.py:589-595` is gone.
5. Test suite is consistent with the new behaviour: `test_provider_registry.py` covers both the registry path and the fallback path; the latently-broken `test_ingest_with_loader_bearer_unknown_provider_returns_403` is removed (no plausible 403 path exists for `LoaderPrincipal` + missing-provider).

### Verification

- Manual: rename a custom OWUI integration provider's slug to `gradient` (or any non-managed-sync value), then run an `/api/v1/integrations/ingest` curl with that service account → expect HTTP 200 with each document at `status: "completed"` (or `created`/`updated`).
- Automated: `python -m pytest backend/open_webui/test/services/sync/test_provider_registry.py backend/open_webui/test/util/test_integrations_loader_auth.py -v` passes.
- Automated: `python -m pytest backend/open_webui/test/util/test_integrations_loader_auth.py::test_ingest_with_loader_bearer_attributes_files_to_acting_user -v` passes (the `onedrive` round-trip, unchanged).

## What We're NOT Doing

- **Not fixing `delete_document` (`routers/integrations.py:822`)** — pre-existing latent bug for `google_drive` (file_ids stored as `googledrive-…` but lookup uses `f'{provider}-…'` = `google_drive-…`). Tangential and out of scope; flag for a follow-up ticket if anyone has hit it.
- **Not adding LoaderPrincipal slug validation** — the loader-worker only sends the three managed-sync slugs in practice; misconfig would fail at stub-creation time anyway. Adding new validation expands scope and risks new regressions.
- **Not changing `INTEGRATION_PROVIDERS` plumbing into `file_id_prefix_for`** — keeping the helper a pure function (slug-in, prefix-out) is simpler than threading `app.state.config` through three call paths.
- **Not changing the managed sync workers** (`services/{onedrive,google_drive,confluence}/sync_worker.py`) — their `file_id_prefix` properties are correct and will keep matching `PROVIDER_FILE_ID_PREFIXES` via the existing `test_registry_matches_worker_prefix` parametrised test.
- **Not changing `get_integration_provider`'s 403** — the regular-user path's per-provider auth check stays as-is.

## Implementation Approach

Two-step, minimal-blast-radius change:

1. **Make `file_id_prefix_for` total.** The registry stays as the slug→prefix override for managed sync providers; for any other slug, fall back to `f'{slug}-'`. This restores the pre-`cc24c435b` invariant for external push providers (slug-as-prefix) while preserving the registry's purpose (Google Drive's slug/prefix divergence).
2. **Remove the redundant upfront 400 check** in `routers/integrations.py`. The regular-user path is already validated by `get_integration_provider` (403 on unknown slug); the LoaderPrincipal path doesn't need this validator.

Tests are updated in lockstep so no commit lands with red CI.

---

## Phase 1: Total `file_id_prefix_for` + Drop Upfront Validator

### Overview

Change one helper, remove one validation block, update two test files. Single commit.

### Changes Required

#### 1. `backend/open_webui/services/sync/provider.py` — make the helper total

**File**: `backend/open_webui/services/sync/provider.py`
**Changes**: Replace the `KeyError → ValueError` branch in `file_id_prefix_for` with a fall-through to `f'{slug}-'`. Update the function docstring and the module-level registry comment to spell out the two-regime semantics (registry for managed sync, fallback for everything else). Adjust the docstring at the top of the file (lines 7-17 currently explain "Do NOT assume slug == prefix.rstrip('-')…this map is the single source of truth" — that statement was true for managed sync only and is now misleading for the fallback case).

```python
# Maps managed-sync provider_slug → file_id_prefix used by that provider's
# worker class when inserting stub File rows. The two strings need not match
# (and don't, for Google Drive) — the loader-worker echoes provider_slug back
# in the /ingest callback, but the stub File row was inserted with
# file_id_prefix. This registry lets the ingest endpoint reconstruct the
# correct file_id for managed-sync round-trips.
#
# External push providers (admin-configured via INTEGRATION_PROVIDERS) have
# no worker class and no stub creation, so there's no slug/prefix divergence
# to override — they default to f'{slug}-' via the fallback in
# file_id_prefix_for() below.
PROVIDER_FILE_ID_PREFIXES: dict[str, str] = {
    'onedrive': 'onedrive-',
    'google_drive': 'googledrive-',
    'confluence': 'confluence-',
}


def file_id_prefix_for(provider_slug: str) -> str:
    """Return the file_id prefix for ``provider_slug``.

    For managed-sync providers (onedrive, google_drive, confluence) returns
    the registry value, which may differ from the slug (Google Drive's
    ``google_drive`` slug maps to the ``googledrive-`` prefix). For any
    other slug — admin-configured external push providers in
    ``INTEGRATION_PROVIDERS`` — falls back to ``f'{slug}-'``, the
    pre-cc24c435b convention where the slug *is* the prefix.

    This is a total function: never raises. Push-provider auth is enforced
    by ``routers.integrations.get_integration_provider`` (403 on unknown
    slug) and KB-creation is gated by ``routers.knowledge``'s
    ``allowed_kb_types`` check; this helper only computes the prefix.
    """
    return PROVIDER_FILE_ID_PREFIXES.get(provider_slug, f'{provider_slug}-')
```

Also update the leading module docstring (lines 1-17) — replace the "Do NOT assume slug == prefix.rstrip('-') — that invariant is no longer enforced anywhere; this map is the single source of truth" sentence with a description of the two-regime semantics so future readers understand the registry is managed-sync-only.

#### 2. `backend/open_webui/routers/integrations.py` — drop the redundant upfront 400 block

**File**: `backend/open_webui/routers/integrations.py`
**Changes**: Delete the block at lines 589-595 (the `try: file_id_prefix_for(provider) except ValueError: raise HTTPException(400, …)` validator). The 4 in-function callsites of `file_id_prefix_for` remain unchanged — they keep working because the helper is now total.

```python
# DELETE these lines (currently 589-595):
#
#     # Reject unknown slugs upfront — an unrecognized X-Acting-Provider header
#     # is a misconfigured loader-worker, not a per-document failure. Surfaces
#     # as a 400 before any DB writes or per-doc loops.
#     try:
#         file_id_prefix_for(provider)
#     except ValueError as e:
#         raise HTTPException(status_code=400, detail=str(e))
```

After deletion, the flow goes straight from the principal-resolution block (lines 574-587) to the `max_per_request` batch-size check (line 598).

#### 3. `backend/open_webui/test/services/sync/test_provider_registry.py` — replace the "raises on unknown" test

**File**: `backend/open_webui/test/services/sync/test_provider_registry.py`
**Changes**: Replace `test_file_id_prefix_for_unknown_raises` with `test_file_id_prefix_for_unknown_falls_back_to_slug_dash`. Add a tightening assertion to `test_round_trip_stub_vs_ingest_file_id` is *not* needed — managed-sync round-trip is the only round-trip the test guards, and that's still correct.

```python
def test_file_id_prefix_for_unknown_falls_back_to_slug_dash():
    """External push providers (no worker class, not in registry) get
    ``f'{slug}-'`` — the pre-cc24c435b slug-as-prefix convention. The
    helper must not raise on slugs missing from the registry; admin-
    configured providers (e.g. ``gradient``, ``octobox``) need to ingest
    too, and their auth is enforced elsewhere (get_integration_provider
    + allowed_kb_types)."""
    assert file_id_prefix_for('gradient') == 'gradient-'
    assert file_id_prefix_for('dropbox') == 'dropbox-'
    # Even an empty string is total — the helper has no business deciding
    # which slugs exist; that's the auth layer's job.
    assert file_id_prefix_for('') == '-'
```

(Drop the existing `test_file_id_prefix_for_unknown_raises` test entirely.)

#### 4. `backend/open_webui/test/util/test_integrations_loader_auth.py` — retire the always-broken test

**File**: `backend/open_webui/test/util/test_integrations_loader_auth.py`
**Changes**: Delete `test_ingest_with_loader_bearer_unknown_provider_returns_403` (lines 351-370). The test asserts a 403 path that does not — and never did, post-`d1e19e901` — exist for the `LoaderPrincipal` + missing-`INTEGRATION_PROVIDERS`-entry combination (the explicit `providers.get(provider) or {}` fallback at `routers/integrations.py:584` is the documented behaviour). After this fix, the same call path returns 200 (or 400 from a downstream KB-not-found / batch-size check, depending on payload) — there is no plausible 403. Removing the test is correct; rewriting it would invent behaviour that doesn't match the design intent recorded in the comment at lines 578-584.

The other tests in the file (`test_ingest_with_loader_bearer_attributes_files_to_acting_user`, the `_create_or_update_file_record`-stub byte-shipping tests etc.) keep passing — they all use `onedrive` and exercise the registry path.

### Success Criteria

#### Automated Verification:

- [x] `python -m pytest backend/open_webui/test/services/sync/test_provider_registry.py -v` — all 5 parametrised + standalone tests pass (3 registry-matches-worker, 3 file_id_prefix_for-returns-registry, 1 fallback, 1 round-trip)
- [x] `python -m pytest backend/open_webui/test/util/test_integrations_loader_auth.py -v` — passes with the unknown-provider test removed
- [x] `python -m pytest backend/open_webui/test/ -k "integration or sync" -v` — no regressions in adjacent integration / sync tests *(collection errors in unrelated files: missing `moto`, stale `MAX_RETRY_COUNT` import, `test.util` namespace — all pre-existing, ran the runnable subset under `test/services` + `test/util/test_integrations_loader_auth.py` + `test/util/test_service_auth.py` = 67 passed)*
- [x] `npm run lint:backend` (PyLint) — no new errors in the two changed Python files (all warnings are pre-existing)
- [x] `npm run format:backend` (Ruff) — files re-format cleanly with no diff

#### Manual Verification:

- [ ] In Admin → Integraties, create an external push provider with slug `gradient` (or similar non-managed-sync value); generate a service-account API key
- [ ] Run the Octobox-style ingest curl from `thoughts/shared/research/2026-03-06-octobox-integratie-email.md` (or equivalent), with `Authorization: Bearer <new-sa-key>` and a small payload (1-2 `parsed_text` documents)
- [ ] Assert HTTP 200, response body shows `provider: "gradient"`, each document at `status: "completed"`
- [ ] Re-run the same curl with the same `source_id` documents — assert each comes back as `status: "updated"` (the in-place re-ingest path; verifies `_create_or_update_file_record`'s update branch fires with the new `gradient-<source_id>` file_id)
- [ ] Confirm the resulting KB appears in `/workspace/knowledge`, `type=gradient`, with the documents listed
- [ ] Re-run an existing managed-sync (OneDrive or Google Drive) sync end-to-end — assert no duplicate File rows are inserted (the original `2026-04-29 incident` does not regress)

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation that the live `gradient`-slug ingest works on a running instance before committing.

---

## Testing Strategy

### Unit Tests

- **Registry path (managed sync, unchanged)**: `test_registry_matches_worker_prefix` parametrised over `(onedrive, google_drive, confluence)` — confirms the registry agrees with each worker class's `file_id_prefix` property. Guards the 2026-04-29 incident.
- **Round-trip (managed sync, unchanged)**: `test_round_trip_stub_vs_ingest_file_id` — confirms stub-side `f'{worker_prefix}{item_id}'` equals ingest-side `f'{file_id_prefix_for(slug)}{item_id}'` for each managed-sync slug.
- **Fallback path (new)**: `test_file_id_prefix_for_unknown_falls_back_to_slug_dash` — confirms unregistered slugs return `f'{slug}-'` instead of raising. Covers `gradient`, `dropbox`, empty string.
- **Existing registry-returns-value (unchanged)**: `test_file_id_prefix_for_returns_registry_value` — parametrised over the registry's three slugs.

### Integration Tests

- `test_ingest_with_loader_bearer_attributes_files_to_acting_user` (existing, unchanged) — exercises the `onedrive` LoaderPrincipal happy path through `/ingest`, asserts `provider`, `user_id`, and `file_id` flow correctly.
- The other byte-shipping tests in `test_integrations_loader_auth.py` (existing, unchanged) — exercise the `original_file` byte upload path; not affected by this change.

### Manual Testing Steps

1. Spin up a local OWUI dev stack (`open-webui dev` + `npm run dev`) with the patched code.
2. Sign in as admin → Admin → Integraties → create new provider, slug=`gradient`, name=`Gradient Test`, `max_documents_per_request=10`, no required custom metadata.
3. Generate a service-account API key under that provider.
4. From a terminal:

   ```sh
   curl -sS -H "Authorization: Bearer <sa-key>" \
        -F 'data={"collection":{"source_id":"sandbox-1","name":"Sandbox","data_type":"parsed_text"},"documents":[{"source_id":"doc-1","filename":"hello.txt","content_type":"text/plain","text":"Hello world."}]}' \
        http://localhost:8080/api/v1/integrations/ingest | jq .
   ```

   Expect HTTP 200, `provider: "gradient"`, `documents[0].status: "completed"` (and `file_id: "gradient-doc-1"`).
5. Re-run with `text` modified — expect `status: "updated"` and the file_id unchanged.
6. Open `/workspace/knowledge` in the browser — confirm the `Sandbox` KB exists with `type=gradient` (visible via API; UI may render it as "external"). Open it, confirm `hello.txt` is listed.
7. From a separate sync KB, run a OneDrive sync end-to-end (Pickfile → sync → wait for completion) — confirm no duplicate File rows by checking `Knowledges.get_files_by_id(<kb_id>)` count equals the number of source files.

## Performance Considerations

None. The change replaces one dict lookup that previously raised on miss with a `dict.get(slug, fallback)` — same O(1) operation. Removing the upfront validator block saves one dict lookup per request. No measurable impact.

## Migration Notes

- **No DB migration**. File rows for external push providers were always stored with `file_id = f'{slug}-<source_id>'` (the pre-`cc24c435b` convention), and the fallback restores that exact convention. Existing data round-trips with no rewrites.
- **No config migration**. `INTEGRATION_PROVIDERS` continues to be the source of truth for which external slugs are valid; this fix doesn't change that surface.
- **Backward compatibility**. Managed-sync KBs continue to use the registry-mapped prefixes (`onedrive-`, `googledrive-`, `confluence-`); no file_ids change. External push KBs continue to use slug-as-prefix; no file_ids change.

## References

- Regression commit: `cc24c435b` ("feat: midway point", 2026-04-30) — see `git show cc24c435b -- backend/open_webui/routers/integrations.py backend/open_webui/services/sync/provider.py`
- Registry rationale (the 2026-04-29 incident): `backend/open_webui/test/services/sync/test_provider_registry.py:60-66` (round-trip test docstring)
- `INTEGRATION_PROVIDERS` config & admin UI: `backend/open_webui/config.py:3355-3359`, `backend/open_webui/routers/configs.py:830-861`
- KB-type allow-list precedent: `backend/open_webui/routers/knowledge.py:283-285` (already accepts arbitrary `INTEGRATION_PROVIDERS` slugs for KB creation)
- Loader-worker/push-provider boundary comment: `backend/open_webui/routers/integrations.py:577-584`
- Octobox integration research (verification curl context): `thoughts/shared/research/2026-03-06-octobox-integratie-email.md`
- Loader-worker design: `thoughts/shared/plans/2026-04-25-shared-services-loader-worker.md`
- Pre-existing latent `delete_document` bug for `google_drive` (out of scope): `backend/open_webui/routers/integrations.py:822` (uses `f'{provider}-{document_source_id}'` instead of `file_id_prefix_for(provider)`)
