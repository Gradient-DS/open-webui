# Request-body audit: what the derivation can and cannot see

Recorded 2026-09-08 while typing the request bodies the derivation could not see
(baseline `6106ef00f` on `feat/owui-attack-plane`). Re-read this before adding a waiver
to `derivation-coverage.toml`: the reasoning per operation is here, the verdicts are there.

The 27 waivers labelled “Untyped JSON request body” were not all untyped.
Reading every handler and its form found 11 bodies with known nested or partly
open shapes, six genuinely free-form bodies, and ten already typed bodies with
only boolean/integer leaves. No invented string fields were added to make the
last group visible to a string-only corpus.

## Decisions for all 27 operations

All paths below are POST operations under `/api/v1/`.

| Operations | Count | Decision and evidence |
| --- | ---: | --- |
| `chats/shared/{id}/access/update`, `folders/{id}/access/update`, `knowledge/{id}/access/update`, `notes/{id}/access/update`, `prompts/id/{prompt_id}/access/update`, `skills/id/{id}/access/update`, `tools/id/{id}/access/update` | 7 | Type the nested grants; remove waivers. Each handler already takes a form with `access_grants: list[dict]`. `normalize_access_grants` consumes `id`, `principal_type`, `principal_id`, and `permission`. Preserve the original form and raw nested dictionaries. |
| `models/import` | 1 | Type the nested model entries; remove waiver. `ModelsImportForm` already wraps `list[dict]`; the handler constructs `ModelForm` after merging existing models. Expose identifiers, name, metadata description/profile URL, operator system prompt, and grants. Keep fields optional because partial updates are valid. |
| `users/user/info/update` | 1 | Type known keys; remove waiver. The handler merges arbitrary keys into `user.info`. The frontend writes `location`; integrations consume `integration_provider`. Keep extra keys permitted and return cached JSON directly. This remains an open object. |
| `users/user/settings/update` | 1 | Type known UI settings; remove waiver. `UserSettings` already wraps an open `ui` dict. Settings declarations and consumers establish system text, model lists, notification webhook URL, tool-server URL/key, title/audio settings, and other named preferences. Preserve the original form, extensions, numeric/string preference representations, and permission filtering. |
| `configs/integrations` | 1 | Type provider values; retain a rewritten waiver. The admin UI defines provider name/description, badge, service-account ID, limits, and metadata-field definitions. Provider slugs are administrator-defined map keys. The derivation deliberately skips `additionalProperties`, so the new schema still derives no paths. Future coverage needs map-key seeding in both derivation and payload construction. |
| `functions/id/{id}/valves/update`, `functions/id/{id}/valves/user/update`, `tools/id/{id}/valves/update`, `tools/id/{id}/valves/user/update` | 4 | Leave free-form; rewrite waivers. The handler loads the installed author's `Valves` or `UserValves` model at runtime. Future coverage must use that selected author schema, not a fabricated global schema. |
| `pipelines/{pipeline_id}/valves/update` | 1 | Leave free-form; rewrite waiver. The handler forwards the supplied key/value body to the selected pipeline service. Keys belong to the pipeline author; coverage needs its runtime schema. |
| `configs/import` | 1 | Leave free-form; rewrite waiver. `ImportConfigForm.config` is an arbitrary exported blob passed directly to `Config.upsert`. Revisit with a versioned import contract or arbitrary-map corpus support. |
| `configs/2fa`, `configs/agent_proxy`, `configs/connections`, `configs/data-retention` | 4 | Keep existing models; rewrite waivers. Respectively: booleans plus grace-period days, one boolean, two booleans, and retention-day integers plus a warning-email boolean. No named string leaves. |
| `users/default/permissions` | 1 | Keep existing `UserPermissions`; rewrite waiver. All nested permission flags are booleans. |
| `channels/{id}/members/active`, `channels/{id}/messages/{message_id}/pin` | 2 | Keep existing forms; rewrite waivers. They contain only `is_active` and `is_pinned` booleans respectively. |
| `folders/{id}/update/expanded` | 1 | Keep existing `FolderIsExpandedForm`; rewrite waiver. Only `is_expanded` (boolean). |
| `archives/admin/config` | 1 | Keep existing `ArchiveConfigForm`; rewrite waiver. Archival booleans and retention-day integers. |
| `auths/admin/config/ldap` | 1 | Keep existing `LdapConfigForm`; rewrite waiver. Only `enable_ldap` (boolean). |

The ten boolean/integer-only waivers should be revisited if string fields are
added or the corpus learns to drive non-string values. They are not free-form
and further typing would not help.

Known fields in open objects are not exhaustive coverage of their extension
data. In particular, arbitrary model parameters, user-info extensions, and UI
extensions remain open even when the route now derives named string fields.

## Payload preservation

The fork-owned `services/remaining_request_bodies.py` follows Phase 4a: validate
a separate Pydantic body, then read Starlette's cached JSON; never serialize the
validation model back into the handler input.

Ten affected handlers already use attribute access on existing form objects.
Their dependencies therefore construct that same original form from cached
JSON, preserving its existing defaults and extra-key policy and leaving nested
dicts uncoerced. Factories accept the router-local form class to avoid circular
imports. The remaining handler, user-info update, continues to receive a raw
dict. Every router edit is one import and one parameter line per operation.

## Measured counts

| Measurement | Before | After |
| --- | ---: | ---: |
| Derived body routes | 230 | 240 |
| Derived writable string fields | 2,525 | 2,591 |
| Derived query routes | 47 | 47 |
| Derived string query parameters | 94 | 94 |
| Waivers / blind bodies | 35 | 25 |
| Blind JSON write bodies | 27 | 17 |
| Blind JSON read bodies | 1 | 1 |
| Blind multipart bodies | 7 | 7 |
| Total operations, including HEAD/OPTIONS | 631 | 631 |

The 66 added string paths are 28 grant paths, 10 model-import paths, two
user-info paths, and 26 UI-setting paths. Eleven improved schemas produce ten
new derived routes because integration provider maps remain unreachable.

## Upstream footprint

Counts are Git additions/deletions, so one replaced parameter is `+1/-1`.

| Router file | Added | Deleted | Parameter lines replaced |
| --- | ---: | ---: | ---: |
| `chats.py` | 2 | 1 | 1 |
| `configs.py` | 2 | 1 | 1 |
| `folders.py` | 2 | 1 | 1 |
| `knowledge.py` | 2 | 1 | 1 |
| `models.py` | 2 | 1 | 1 |
| `notes.py` | 2 | 1 | 1 |
| `prompts.py` | 2 | 1 | 1 |
| `skills.py` | 2 | 1 | 1 |
| `tools.py` | 2 | 1 | 1 |
| `users.py` | 3 | 2 | 2 |
| **Total** | **21** | **11** | **11** |

`archives.py`, `auths.py`, `channels.py`, `functions.py`, and `pipelines.py`
have zero changed lines. No static assets changed.

## Validation

- The baseline derivation tests passed (five tests).
- Before implementation, 12 HTTP controls passed against the unchanged
  handlers, covering all 11 selected operations and both settings permission
  branches. The controls post realistic bodies with extra keys and assert
  downstream calls and payloads, including preserved partial-import ACLs.
- The same 12 controls passed after adding dependencies.
- Four expanded settings controls (string/numeric values crossed with
  admin/user permissions) passed with the original settings parameter restored
  before widening the new numeric-preference fields. All 14 final HTTP controls
  pass with the new dependencies.
- Eleven schema/derivation checks establish the new named fields and explicitly
  assert that provider maps have typed values without invented slugs.
- The final security suite passed: **367 tests**, with 30 existing dependency
  and import warnings. The suite includes fresh-spec drift and static-asset
  protection checks. Ruff passes for the two new Python files; `git diff
  --check` passes.
- `scripts/security/export_openapi.py` generated the spec in its documented
  offline SQLite/embedded-Chroma environment. Only the intended 11 operations'
  request bodies changed; operation keys and all other operation properties
  stayed equal to baseline.

The supplied interpreter was used read-only with this worktree's backend on
`PYTHONPATH`. Missing `openapi-surface` (the repository-pinned revision) and
`joserfc` were installed only into a throwaway `/private/tmp` target. Temporary
application data stayed in the worktree. No Docker, push, PR, merge, or tag was
used. There were no blocked checks; the remaining coverage limits are recorded
above and in `derivation-coverage.toml`.
