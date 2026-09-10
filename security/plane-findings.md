**Status: 12 open findings (PLANE-001–010, PLANE-012, PLANE-013), 1 fixed
(PLANE-011); BLOCKER-001 resolved; CI-stack gaps: 1 open (CI-004), 3 fixed
(CI-001–003).**

**The coverage gate is live and red at 96 routes, and 50 of them were the
harness, not the application.** `test_every_route_is_driven_or_waived` merged
2026-09-10 (PR #290) and `security/route-coverage.toml` still does not exist.
Classifying the 96 by what the owner-driving passes actually observed:

| Cause | Count |
| --- | --- |
| 422 only — attacked repeatedly, body refused before the handler | 50 |
| 404 seen — feature off or resource absent | 23 |
| never attempted — skipped by every owner pass | 19 |
| 403 seen, or other | 4 |

The 50 were not a property of the application. `writable_string_fields` reports
string leaves, so a body built from it omits every required bool, int, dict and
nested scalar the schema also demands —
`POST /api/v1/images/config/update` was driven 91 times and entered zero,
`POST /api/v1/auths/admin/config` 58 times for 16 missing `ENABLE_*` booleans.
Waiving those would have been the gate recording its own blind spot as a
decision. The fix is a schema-derived body skeleton
(`Gradient-DS/.github#17`); measured route by route, **38 of the 50 then enter a
handler**. Of the remainder, five are multipart uploads needing a different code
path in the driver, five answer 404 for want of a seeded *body* field (the seed
mechanism covers path parameters only), and two are refused by Pydantic
validators the spec does not express.

**Landing it is blocked on PLANE-013**, which the same change exposed: once
payloads reach configuration handlers, the pass's own configuration poisoning
takes effect and unloads the embedding model, so a full run can no longer
complete. A control on unmodified `dev` shows the poisoning is older than this
work.

Two of the 96 remain a judgement call rather than a measurement:
`POST /api/v1/auths/signin` and `POST /api/v1/configs/import` are used constantly
by the identity helper and the configuration guard — the harness using a route is
not a security test driving it, and for those the answer may be to drive them
rather than waive them.

**The live 5xx assertions are EXPECTED to be red while application findings are open.**
Latest CI run (2026-09-10, first run with every pass completing on v0.11.3):
**9 failed, 473 passed, zero errors**; offline **724 passed, 63 skipped** under
both `-q` and `-v`. Per-pass reach — seeding 191/252, drive 354/662, shapes
266/372, query 48/54, crossuser 260/415 — matches a workstation run almost
exactly, which is new: before PR #290 the drive pass never ran in CI at all.

Eight failures are a pass's own 5xx assertion or the model-output control. The
ninth, `test_live_shapes_reports_its_own_reach`, is the gate telling the truth:
shapes loses about four configuration snapshots per CI run to dropped connections
on `GET /api/v1/configs/export`, so `config_restore_verified` is false. **That
makes it a blocker for `enforce: true`** — a check cannot be required while it is
permanently red for an environmental reason. The cause of the drops
(`ConnectionError <- MaxRetryError <- ProtocolError <- RemoteDisconnected`) is
unknown and does not reproduce off a hosted runner. CI captures Falco's logs but
not the application's, so there is nothing to diagnose from; adding app-log
capture to `Gradient-DS/.github` is the prerequisite.

**`runtime-audit` reports pass regardless, because `enforce: false`.** Every
result in this leg had to be read inside the job. The first green check of the
leg sat over a run in which the drive pass never executed.

An earlier reviewer run recorded **4 failed, 381 passed, 9 errors in 350s**. The
abbreviated log was insufficient to reconcile the route union, so this document
retains all supplied routes without inventing a current set.

Quick comparison for a red gate:

| Known route / behavior | Finding | Current status |
| --- | --- | --- |
| Configuration poisoning and downstream upload/ingestion failures | PLANE-001 | Open application bug; retained from earlier runs |
| Ingestion HTTP 200 with `created: 0, errors: 1` | PLANE-002 | Open application bug; body failure, not itself a 5xx |
| `GET /api/v1/notes/`, `GET /api/v1/notes/search` | PLANE-003 | Open application bug; search surfaced on latest run |
| `POST /api/embeddings`, `POST /api/v1/embeddings` | PLANE-004 | Open application bug; latest run |
| `POST /api/v1/data-warnings/accept` | PLANE-005 | Open application bug; latest run |
| `POST /api/v1/images/generations` | PLANE-006 | Open application bug; newly recorded from latest run |
| `GET /api/v1/discovery/documents` | PLANE-007 | Open, cause unresolved; newly recorded from latest run |
| `GET /api/v1/chats/archived`, `GET /api/v1/chats/shared`, `GET /api/v1/chats/list/user/{user_id}` | PLANE-008 | Open; shared sorting failure path, live traceback needed |
| `POST /api/v1/tasks/{follow_up,title,tags,image_prompt}/completions` | PLANE-009 | Open; shared message-template failure path, live traceback needed |
| `GET /api/v1/utils/gravatar` | PLANE-010 | Open; cause unresolved |
| `POST /api/v1/export/data` never becomes ready | PLANE-011 | Application bug, fixed 2026-09-10; merge regression in fork code |
| `POST /api/v1/knowledge/metadata/reindex` exceeds the 30s client timeout | PLANE-012 | Open; cost is unbounded in the number of knowledge bases |
| Terminal and indexed Ollama DELETE | CI-001 | CI-stack gap confirmed fixed; recurrence needs investigation |
| `POST /api/v1/notifications/targets` seeded but gated off | CI-003 | Declaration gap confirmed fixed |

Payload sampling varies per run: the routes surfaced by one sampled run are not
the full failure set. This file accumulates findings across runs; absence from
the latest run does not close a finding. A listed route with the documented
failure signature is known-open; an unlisted route, a different cause/status/body,
or a recurrence of CI-001 needs investigation as potentially new evidence.
PLANE-007 is a known observation, not a settled cause. A matching total of
failed tests alone cannot distinguish old failures from new ones.

RUN-TAG: owui-phase7b-fix-live-defects

## PLANE-001: Unvalidated configuration writes poison typed downstream consumers

Status: **open, to be fixed on dev**. Reported by the live reviewer on
2026-09-08; this branch changes only the attack plane, not the application.

Configuration accepts corpus strings that later consumers interpret as integers,
engine names, enums, or URLs. A write can persist before its handler raises;
restoring only a known upload key leaves other features broken across passes.
Both reviewer passes (previously poisoned and pristine stacks) ended with about
40 setup errors from the same integration seed after embedding-engine poisoning.
These are application findings, not reasons to weaken that seed's assertions.

The full **currently confirmed live list** is below. No live snapshot/diff is
available in this sandbox, which blocks loopback. This list is **not a claim that
only two keys are writable**. Additional keys and their downstream failures
cannot honestly be enumerated as live observations until the reviewer runs the
new diff recorder. Schema fields alone do not prove persistence.

| Config key | Write path | Consumer and breakage |
| --- | --- | --- |
| `rag.file.max_size` | `POST /api/v1/retrieval/config/update`, `FILE_MAX_SIZE`; arbitrary import also accepts this key | `routers/files.py:296,303` reads it then calls `int(max_size_mb)`. Non-numeric strings cause HTTP 500 on subsequent multipart uploads with Content-Length. |
| `rag.embedding_engine` | `POST /api/v1/retrieval/embedding/update`, `RAG_EMBEDDING_ENGINE`; arbitrary import also accepts this key | `routers/retrieval.py:239,536` maps and persists it before constructing the embedding function. Ingestion constructs the function at `routers/retrieval.py:1709`; `retrieval/utils.py:1170` raises `ValueError: Unknown embedding engine`. `routers/integrations.py:669` catches it and reports a document error; vector storage fails. |

The embedding value observed by the reviewer was
`<style>@import url("http://egress-canary.invalid/gdsprobe7f3a");</style>`.
The consumer interprets it as an engine name; this evidence establishes ingestion
denial of service, not a successful CSS fetch or outbound request.

<!-- config-corpus-diff:start -->
Complete live seeding diff pending reviewer execution. Run `cicd/config_findings.py`
as documented in `plane-verification.md` to insert every changed config key here (including transformed and collateral values).
Each new key requires source/consumer review before claiming downstream breakage.
<!-- config-corpus-diff:end -->

Source locations relative to `backend/open_webui/`:

- `routers/retrieval.py:882` accepts `FILE_MAX_SIZE` as `int | str | None`.
  `routers/retrieval.py:1152` retains any nonempty string and
  `routers/retrieval.py:1258` writes it to `rag.file.max_size`.
- `routers/configs.py:102` also accepts arbitrary values through config import.
- `routers/files.py:296` reads the stored value; `routers/files.py:303` calls
  `int(max_size_mb)` without guarding conversion errors. The neighbouring
  Content-Length conversion is guarded, but the stored limit conversion is not.

An authenticated admin can persist a non-numeric upload limit. Every subsequent
multipart upload with Content-Length then fails with HTTP 500 for every user,
until the value is repaired. The reviewer observed the stored value
`![x](file:///etc/passwd#gdsprobe7f3a)` and its `ValueError` in the live stack.
This finding is a stored-value denial of service; it does not establish file read.

Exact reproduction against the affected live build (use valid admin and user
bearers in `ADMIN_TOKEN` and `USER_TOKEN`, and the stack URL in `ATTACK_BASE_URL`):

```sh
curl --fail-with-body -X POST "$ATTACK_BASE_URL/api/v1/retrieval/config/update" \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  --data '{"FILE_MAX_SIZE":"![x](file:///etc/passwd#gdsprobe7f3a)"}'
curl --fail-with-body "$ATTACK_BASE_URL/api/v1/configs/export" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
curl -i -X POST "$ATTACK_BASE_URL/api/v1/files/?process=false" \
  -H "Authorization: Bearer $USER_TOKEN" \
  -F 'file=@security/plane-findings.md;type=text/plain'
```

The export contains `"rag.file.max_size":"![x](file:///etc/passwd#gdsprobe7f3a)"`;
the upload returns 500 and logs
`ValueError: invalid literal for int() with base 10: '![x](file:///etc/passwd#gdsprobe7f3a)'`.
The import equivalent is `POST /api/v1/configs/import` with
`{"config":{"rag.file.max_size":"![x](file:///etc/passwd#gdsprobe7f3a)"}}`.
After reproduction, restore the original limit through config import (or set
`{"config":{"rag.file.max_size":10}}` for a finite 10 MiB recovery limit).

Plane containment: `attack/configuration.py` snapshots the entire config export,
diffs every exported key (including nested values and JSON types), imports all
changed original values, then verifies equality of the complete export. It wraps
**both complete passes** in `plane.py`, and every mutating request inside them.
Per-request recovery prevents early config writes from poisoning later routes
within the same pass; the outer `finally` also catches changes made by read
routes. Every route and all derived fields still run. Recovery requests do not
contribute route hits. Each pass records all changes in `config_findings`, their
union in `config_keys`, and the subset with recognized corpus values in `corpus_config_keys`.
`restored` in an individual record is the intended original value, retained even
when recovery fails. `config_restore_verified` is true only after the whole pass
exits its guard successfully. Even 500 responses and lost responses get recovery.

A mode-0600 journal, keyed by stack URL under `.cache/attack/`, survives interrupted
passes. `resolve_parameters` replays it before any fixture creation. For older
runs with no journal, it detects corpus strings recursively in **all** config
values (including known URL origins after path normalization) and restores contaminated keys from `ATTACK_CONFIG_BASELINE`. The baseline
is generated in a new process under the container's environment by
`cicd/config_baseline.py`; it is not an export of the poisoned process. Valid
existing settings are preserved. Recovery is serialized; do not run two planes
against one stack or carry a pending journal to a replacement stack at that URL.

Limits are explicit: HTTP import merges and cannot delete a newly introduced
config key. The guard restores every original key, then fails loudly and retains
the journal if the final export is not exactly equal. It never silently equates
absence with null. Existing CI defaults cover registered keys; arbitrary new-key
injection needs an application-supported removal operation for exact HTTP
recovery. Export equality verifies config values, not every derived in-memory
cache or downstream service. The subsequent real upload/integration fixtures
remain the usability checks. No application validation or handler was changed.

## PLANE-002: Integration ingest returns HTTP 200 when document ingestion fails

Status: **open, to be fixed on dev**. Reviewer-confirmed on both live passes.

`POST /api/v1/integrations/ingest` returned HTTP **200** with **`created: 0,
errors: 1`** when the poisoned embedding engine prevented vector storage.
`routers/integrations.py:669-676` catches the vector-store exception and returns
a document result with `status: error`. `ingest_documents` counts these results
and returns an ordinary dict (`1018-1033` for file targets, `1211-1215` for
knowledge targets), with no non-2xx status when all documents fail. A caller
checking only HTTP status therefore mistakes complete ingestion failure for
success. This is separate from the config validation problem: other document
errors follow the same reporting path.

`seed_integration` correctly rejects positive errors and requires a created
attachment. Those assertions are unchanged. Plane-driven 2xx ingest responses
with a positive `errors` count are also retained in `body_failures` as PLANE-002;
handler entry and the explicitly HTTP-only `accepted` counter remain distinct
from successful ingestion. Reproduce by importing the observed bad embedding
engine and submitting the seed's chunked-text multipart ingest request; restore
the config snapshot afterwards. No endpoint status behavior was fixed here.

## PLANE-003: Stored note content breaks subsequent note listings and search

Status: **open, to be fixed on dev**. The live reviewer reported
`GET /api/v1/notes/` among the failures on both earlier 2026-09-08 passes,
and `GET /api/v1/notes/search` on the latest run. Both consume stored data
through `_truncate_note_data` and belong to this same finding.

Credit: the reviewer identified this as **second-order, the same shape as
PLANE-001**: a malformed value is stored, read back from the database later,
and consumed without checking its type. This is a persistent listing failure
for the affected user until the offending note is repaired or removed; it is
not merely rejection of the original write.

Source locations relative to `backend/open_webui/`:

- `models/notes.py:65-69` allows arbitrary dictionary contents in `NoteForm.data`;
  `140-144` inserts that JSON and commits it.
- `routers/notes.py:85` loads stored notes for the listing and `99` passes
  `note.data` to `_truncate_note_data`.
- `routers/notes.py:45` evaluates `(data.get('content') or {}).get('md')`.
  A nonempty string at `data.content` raises
  `AttributeError: 'str' object has no attribute 'get'`, escaping the GET handler
  as HTTP 500. The search route reads stored notes at `193` and calls the
  helper at `197`; the root listing calls the same helper at `99`.

The reviewer described a note created with string `data`. Preserve that as the
live report, with this source discrepancy explicit: this checkout declares
top-level `data` as `Optional[dict]`, so a top-level string is rejected by
Pydantic. A **nested string at `data.content`** is accepted and reaches the
same failing expression. Offline execution of the actual form/helper definitions
confirmed both the top-level rejection and the nested-string `AttributeError`.
The original stored JSON and write request were not supplied; determining their
exact shape requires the live artifact/database. Do not infer that the current
schema accepts a top-level string.

Exact minimal reproduction for this checkout, using the same admin bearer for
creation and listing (HTTP effects still require reviewer verification):

```sh
curl -i -X POST "$ATTACK_BASE_URL/api/v1/notes/create" \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  --data '{"title":"plane-003","data":{"content":"stored-string"}}'
curl -i "$ATTACK_BASE_URL/api/v1/notes/" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
curl -i "$ATTACK_BASE_URL/api/v1/notes/search?query=plane-003" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

Expected: creation persists the note; both subsequent GETs return 500 with the
exception above in the server log. Retain the created note ID and remove it
after reproduction via `DELETE /api/v1/notes/{id}/delete` using the same bearer.

## PLANE-004: Both embeddings aliases raise an unhandled exception for unknown models

Status: **open, to be fixed on dev**. Both `POST /api/embeddings` and
`POST /api/v1/embeddings` returned 5xx in the reviewer's repeated passes.
They are one finding because they register the same handler and dispatcher.

`main.py:1323-1325,1342-1345` registers both aliases, loads the model registry
if empty, and calls `generate_embeddings` without catching its exceptions.
`services/model_request_bodies.py:128-131,150-151` permits arbitrary or absent
model strings and returns the original JSON. `utils/embeddings.py:63-65` raises
plain `Exception('Model not found')` when that model is absent from the registry,
turning invalid model selection into HTTP 500 rather than an application 4xx.
This happens before dispatch to either upstream provider; the embedding stub's
response format and the recovered `rag.embedding_engine` are not the cause of
this source-established failure path.

The seeding pass replaces `model` with corpus strings and also tries its
omission. An unregistered model therefore reaches this exception. The supplied
review report contains route names but no embeddings traceback; attribution
of the particular live responses to this path is source-based, not a newly
observed live exception.

Exact minimal reproduction with a model name absent from the registry:

```sh
for route in /api/embeddings /api/v1/embeddings; do
  curl -i -X POST "$ATTACK_BASE_URL$route" \
    -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
    --data '{"model":"plane-004-unregistered-model","input":"probe"}'
done
```

Expected: both aliases return 500 and log `Exception: Model not found`.
No malformed or malicious input text is needed, and no state repair is needed
for these two requests. These HTTP reproductions were not run in this sandbox.

## PLANE-005: Data-warning acceptance commits an audit row then fails ORM validation

Status: **open, to be fixed on dev**. The reviewer observed
`POST /api/v1/data-warnings/accept` returning 5xx on both passes.

`routers/data_warnings.py:18,29` calls `DataWarningLogs.insert_log` when
`features.enable_data_warnings` is enabled (the default at `config.py:2840`).
`models/data_warnings.py:58-61` adds, commits and refreshes the SQLAlchemy
`DataWarningLog`, then calls `DataWarningLogModel.model_validate(log)`.
The response model at `models/data_warnings.py:33-40` lacks
`ConfigDict(from_attributes=True)` and the call does not pass
`from_attributes=True`. Pydantic rejects the ORM instance with a `model_type`
ValidationError; the handler does not catch it, so the client receives HTTP 500
**after the row was committed**. Retrying can insert another audit row.

Offline execution of the actual response-model definition with an
attribute-bearing object confirmed the `model_type` failure. A stack traceback
is still needed to confirm that the reviewer's particular requests reached
this line rather than failing earlier in the database. No database failure or
missing migration is assumed. With the feature disabled, the handler constructs
the model from keyword arguments and this failing path is bypassed.

Exact minimal reproduction, with `features.enable_data_warnings=true`:

```sh
curl -i -X POST "$ATTACK_BASE_URL/api/v1/data-warnings/accept" \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  --data '{"chat_id":"plane-005-chat","model_id":"stub-model","capabilities":["chat"],"warning_message":"plane-005"}'
```

Expected: HTTP 500, `DataWarningLogModel` validation error with type `model_type`
in the server log, and a committed `data_warning_log` row for `plane-005-chat`.
The form and table impose no chat/model foreign key requirement. The exact
HTTP request and committed-row check require the reviewer stack; they were not
run here. This is independent of hostile corpus content.

## PLANE-006: Image size parsing raises before upstream generation

Status: **open application bug, to be fixed on dev**. The latest reviewer run
reported `POST /api/v1/images/generations` returning **500** with the literal
body **`Internal Server Error`**. The recovered frames are
`routers/images.py:576` in `generate_images`, awaiting `image_generations`, and
`routers/images.py:611` inside `image_generations`. The final exception text and
original request were not recovered.

`CreateImageForm.size` at `routers/images.py:448` is an unconstrained optional
string. Lines `604-609` choose a configured or request size whenever it contains
`x`; line `611` executes `width, height = tuple(map(int, size.split('x')))`,
outside the `try` that starts at `617`. Thus a hostile request size such as
`<img src=x>` raises `ValueError: invalid literal for int() with base 10:
'<img src='`; `1x2x3` raises `ValueError: too many values to unpack (expected 2)`.
These failures escape as 500 before model selection or any upstream call.
A poisoned configured `IMAGE_SIZE` containing `x` can also reach the same line;
the missing live request/config snapshot prevents choosing between those inputs
or claiming the exact original exception message.

This is **the application, not a missing stub route; no CI-002 is warranted**.
Compose selects engine `openai` and base URL `http://stub:8000/v1`. The application
would POST to `/v1/images/generations` at lines `627,655-661`, but cannot reach
that code after a parsing failure. `cicd/stub_upstream.py` already implements
that route and returns OpenAI-shaped `data[].b64_json` with an inline PNG.
The existing offline `test_generated_image_is_inline_and_decodable` covers that response.

Offline execution of the actual form and `image_generations` definitions
(extracted with AST; config lookup replaced by a valid `512x512` setting)
confirmed both ValueErrors above and that model selection was never reached.
No application code was changed. Minimal live reproduction:

```sh
curl -i -X POST "$ATTACK_BASE_URL/api/v1/images/generations" \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  --data '{"prompt":"plane-006","size":"<img src=x>","n":1}'
```

With image generation enabled, expect HTTP 500, body `Internal Server Error`,
and a ValueError at line `611`. No state repair is needed for this request.
Capture the actual request size, configured `IMAGE_SIZE`, and final traceback
line to pin the original sampled input. This HTTP reproduction was not run here.

## PLANE-007: Discovery document catalog returns an unexplained HTTP 500

Status: **open, cause unresolved**. This is a known failing application route,
not yet a demonstrated application defect or CI-stack gap. The latest reviewer
run reported `GET /api/v1/discovery/documents` returning **500**. Only
`Exception in ASGI application` was recoverable from the logs; no handler frame,
exception type/message, response body, or exact sampled request was supplied.

Source review of the fork-owned `routers/discovery.py` does **not** establish
the cause:

- `list_documents` at `141-148` declares no request body or query parameters;
  it checks config and proxies a fixed `/v1/discovery/documents` path. There is
  no visible hostile-string conversion analogous to the image or notes bugs.
- `_get_base_url` at `43-60` awaits the implemented `Config.get` method
  (`models/config.py:157-163`). Disabled `rag.enable_filter_ui` or an empty
  `SEARCH_API_BASE_URL` raises explicit **503**, not the observed 500. Compose
  supplies `SEARCH_API_BASE_URL=http://stub:8000`; the live persisted flag still
  needs inspection. Do not relabel this 500 as the disabled-feature response.
- `_proxy_get_json` at `78-137` maps connection/client errors during the request
  to 502 and timeouts to 504; upstream 401 and JSON decode failures become 502.
  Other upstream error statuses pass through. The current stub already serves
  `/v1/discovery/documents` with HTTP 200 and
  `{"collections":[],"total_collections":0,"database":{}}`.
- Exceptions in config lookup, session construction, response-body reading,
  or session close are not all covered by those handlers. Those are possible
  investigation points, not evidence that any one occurred on the live run.

Offline execution of the actual `_proxy_get_json` and `_auth_headers`
definitions against a temporary loopback instance of the current stub returned
the catalog JSON successfully. This bypasses authentication, the config DB,
application middleware, and the container network/environment; it cannot settle
the live failure. There is no source-supported stub fix to make here.

To settle it, replay the recorded GET with the same bearer and sampled request
in the existing stack and capture the complete exception chain, including the
first application frame and final exception type/message. Save the response
status/body, request timestamp/pass, and deployed revision alongside it. Inspect
`rag.enable_filter_ui` through config export and the running process's
`SEARCH_API_BASE_URL`; request `http://stub:8000/v1/discovery/documents` from the
application container to record the actual upstream status/body and connectivity.
Do not include API keys or bearer values in the report.

```sh
curl -i "$ATTACK_BASE_URL/api/v1/discovery/documents" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

This is a baseline probe, not an established exact reproduction of the sampled
failure. With the flag enabled and healthy config/auth/network, the reviewed
source and current stub should return the catalog JSON. A 503 with the documented
feature-disabled message is a separate configuration condition. If the original
500 does not recur, retain this finding until its recorded request and stack
state explain it; a passing probe alone does not close it.

## PLANE-008: Chat list sorting inputs raise uncaught exceptions

Status: **open, to be fixed on dev**. The reviewer reports 5xx for
`GET /api/v1/chats/archived`, `GET /api/v1/chats/shared`, and
`GET /api/v1/chats/list/user/{user_id}` after enabling the query pass.
Exact per-route statuses, query values, response bodies and tracebacks were not
supplied. Source locations below are relative to `backend/open_webui/`.

Grouped by the shared sorting-input mechanism:

- `routers/chats.py:603,904,1012` accepts unrestricted `order_by` and `direction`
  strings and forwards them in a filter at `630,927,1035` respectively.
- Archived: `models/chats.py:979–988` raises `ValueError` for an unknown sort
  field or invalid direction. User list: `models/chats.py:1061` uses
  `getattr(Chat, order_by)` without a default, raising `AttributeError` for an
  unknown field; `1067` raises `ValueError` for an invalid direction.
- Shared: `models/shared_chats.py:153–162` likewise raises `ValueError` for an
  unknown field or direction. These handlers do not translate those exceptions
  into a client error. The query pass supplies both fields in its combined probe,
  satisfying the condition required to enter these branches.

Source-derived reproduction (use the plane's seeded user ID for `SEEDED_USER_ID`
and its admin bearer; admin chat access must be enabled for the user-list route):

```sh
for path in archived shared "list/user/$SEEDED_USER_ID"; do
  curl -i -G "$ATTACK_BASE_URL/api/v1/chats/$path" \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    --data-urlencode 'order_by=plane_invalid_sort' --data-urlencode 'direction=asc'
done
```

Expected source failure: `Invalid order_by field` on archived/shared, and an
`AttributeError` on the user list. A second probe with `order_by=updated_at` and
`direction=plane_invalid_direction` exercises the explicit direction errors.
These branches establish a common failure mechanism, but this sandbox cannot
confirm that they produced every reported live 5xx. Retain the sampled requests
and full container exception chains to match the observations; a DB or middleware
exception would require separate attribution.

**Also observed 2026-09-08, same shape:**
`GET /api/v1/evaluations/feedback/conversation/{chat_id}` answers 500
(`HTTP {500: 2}`, body `Internal Server Error`). It matters beyond its own fault:
it is an OWNER positive control in the authorization pass, so while it faults
that pass reports `isolation remains unproven` for the route rather than
claiming a pass. A control that cannot succeed cannot demonstrate isolation.

## PLANE-009: Task completion templates consume incompletely validated messages

Status: **open, to be fixed on dev**. The reviewer reports 5xx on
`POST /api/v1/tasks/follow_up/completions` (**HTTP {500: 16}**),
`POST /api/v1/tasks/title/completions`,
`POST /api/v1/tasks/tags/completions`, and
`POST /api/v1/tasks/image_prompt/completions`. Bodies, sampled fields and
tracebacks were not supplied. Grouped by the shared pre-completion template path;
this is source-supported reproduction, not a claim that all live exceptions
have already been matched to this cause.

`services/model_request_bodies.py:35–47,76–89,138–139` permits content blocks with no
`type` and returns the original request dictionary through the task dependency.
The four handlers call their templates at `routers/tasks.py:171,252,324,390`,
before the final completion exception handlers. Their templates at
`utils/task.py:304–339` all call `get_last_user_message` and
`replace_messages_variable`. `utils/misc.py:164–168` indexes `item['type']`
and then `item['text']` without checking presence. A schema-valid content block
containing only `text` therefore raises `KeyError: 'type'` before model execution.

Offline AST execution of the actual four template functions and message helpers
reproduced that exception for every template. This check bypasses HTTP,
authentication, configuration, model selection and middleware; it establishes
the shared source mechanism but cannot identify the reviewer's 16 exceptions.

Source-derived reproduction (use a discovered model ID in `MODEL_ID`; title,
follow-up and tags generation must be enabled in the existing stack):

```sh
for task in follow_up title tags image_prompt; do
  curl -i "$ATTACK_BASE_URL/api/v1/tasks/$task/completions" \
    -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
    --data "{\"model\":\"$MODEL_ID\",\"messages\":[{\"role\":\"user\",\"content\":[{\"text\":\"probe\"}]}]}"
done
```

Expected source failure with those prerequisites: uncaught `KeyError: 'type'`.
Save the actual request, active task configuration and full traceback from the
reported run before attributing its failures. In particular, the tags handler
also converts final completion exceptions to HTTP 500 at `routers/tasks.py:347–351`;
the other three convert exceptions in that final block to HTTP 400. A tags 500
alone cannot distinguish template failure from that separate downstream path.

## PLANE-010: Gravatar lookup returns a live 5xx with unresolved cause

Status: **open, to be fixed on dev**. The reviewer supplied the truncated route
`GET /api/v1/utils/gravatar` in the new 5xx set, without its request or traceback.
The exact method/template is confirmed by `routers/utils.py:38–40` and the
`/api/v1/utils` mount at `main.py:1077`; there is no path parameter or trailing
slash in the declaration. `email` is a required string query parameter.

The handler calls `utils/misc.py:659–670`, which strips/lowercases the email,
encodes it, computes SHA-256 and returns a Gravatar URL. It performs no upstream
image request. Offline execution with an ordinary email returns that URL.
The supplied evidence does not establish why the live request returned 5xx;
do not attribute it to Gravatar connectivity or a particular encoding,
authentication or middleware error without the exception chain.

Baseline probe, not an established exact reproduction of the reported failure:

```sh
curl -i -G "$ATTACK_BASE_URL/api/v1/utils/gravatar" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  --data-urlencode 'email=probe@example.com'
```

With healthy authentication/middleware the reviewed source returns HTTP 200
with a JSON URL string. Replay the query pass's exact sampled `email`, retaining
request timestamp, method/path/query, response status/body, deployed revision and
complete application traceback. A passing baseline does not close the recorded
finding. Cause remains unresolved without the stack.

## CI-001: Missing stub DELETE produces upstream 501 responses

Scope: **CI stack gap, not an application finding**. Status: **confirmed fixed**.
The reviewer restarted the stub and reran live: **2 failed, 307 passed in 252s**.
Both routes below disappeared from the 5xx set, confirming that the missing
DELETE handler accounted for both failures, including the provisional Ollama
attribution. Historical mechanism and reproduction:

- `DELETE /api/v1/terminals/{server_id}/{path}`: the reviewer supplied **501,
  entered=True, HTML body**. `routers/terminals.py:152-153` forwards
  `request.method` unchanged; `184-190` returns the upstream status and body.
  The plane registers the terminal at `http://stub:8000` and resolves `path`
  to `openapi.json` (`security/attack-surface.toml`, terminal/terminal_path).
  Before this change, `cicd/stub_upstream.py:Handler` had no `do_DELETE`.
  Python's `BaseHTTPRequestHandler` therefore emitted its unsupported-method
  HTML 501. Handler entry proves the proxy ran, not that it generated the 501.
- `DELETE /ollama/api/delete/{url_idx}`: `routers/ollama.py:784-790` selects
  the configured URL and calls `send_request` with method `DELETE` and path
  `/api/delete`. The plane resolves index `0`, which compose points at the
  same stub. `routers/ollama.py:134-160` converts a non-JSON upstream error to
  an `HTTPException` retaining its status. Thus the missing stub method also
  produces a 501 here, with an application JSON error envelope. The original
  exact status/body was unavailable, but the post-restart live run now confirms
  the attribution. The unchecked index at `784` is not evidence of an
  out-of-range index in these seeded runs.

Fix: `cicd/stub_upstream.py:255-257,343-345` handles DELETE through the existing
body parser/capture and returns JSON `{"status":"ok"}`. As with the stub's
other unmodelled services, this is a stateless acknowledgement; it does not
remove its advertised model or OpenAPI document. No application handler changed.

Exact reproduction before/after the fix from inside the CI network
(`STUB_BASE_URL=http://stub:8000`; no public stub port is required):

```sh
curl -i -X DELETE "$STUB_BASE_URL/openapi.json"
curl -i -X DELETE "$STUB_BASE_URL/api/delete" \
  -H 'Content-Type: application/json' --data '{"model":"stub-model"}'
```

Before: both return HTML 501. After: both return HTTP 200 JSON
`{"status":"ok"}`; offline loopback tests cover these actual wire requests,
body capture and continued model discovery. To verify through the application,
use the terminal ID registered by the plane in `TERMINAL_SERVER_ID`:

```sh
curl -i -X DELETE "$ATTACK_BASE_URL/api/v1/terminals/$TERMINAL_SERVER_ID/openapi.json" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
curl -i -X DELETE "$ATTACK_BASE_URL/ollama/api/delete/0" \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  --data '{"model":"stub-model"}'
```

For a stack that has not yet loaded the fix, restart the stub process:
`docker compose -f docker-compose.ci.yaml restart stub`.
Expected direct responses are terminal 200 JSON and indexed Ollama 200 (`true`)
if its remaining application/event path succeeds. The reviewer confirmed absence
from the 5xx set, not these exact successful bodies. No 5xx exception
or accepted-finding entry was added to the plane gate or expectations.

## CI-002: the stub answered only some forwarded methods

Status: **fixed**. `routers/terminals.py` forwards every method in
`PROXY_METHODS`; the stub implemented GET, POST, DELETE and PUT, so PATCH (and
OPTIONS, HEAD) fell through to BaseHTTPRequestHandler's 501 HTML error page,
which the proxy returned as a 5xx indistinguishable from an application fault.
Recorded because it was briefly mistaken for one: `PATCH /api/v1/terminals/...`
appeared in the 5xx set with `HTTP {501: 7}` and an HTML body.

## PLANE-011: data export starts, never completes, and reports success

Status: **fixed 2026-09-10** in this branch, against the plan's usual rule of not
fixing application bugs alongside the plane. It is recorded as an exception
because it is a regression the v0.11.3 merge introduced into fork-owned code —
the same merge this branch's gate refresh is about — and because it blocked every
live pass, so the plane could not run at all while it stood.

Upstream #28795 (`4df2d9a7a`) removed `ModelsTable.get_models_by_user_id`, folding
the same owner-or-writable filter into `get_models` in SQL. The fork-owned export
service (`services/export/service.py:110`) still called the removed method, so the
background job raised for every user:

    Data export failed for user <id>: 'ModelsTable' object has no attribute
    'get_models_by_user_id'

`POST /api/v1/export/data` answers 200 with `status: processing`, and
`GET /api/v1/export/data/status` then answers `status: none` forever. From the
user's side the GDPR export silently never arrives, with no error anywhere they
can see. Fixed by calling `Models.get_models(writable_by_user_id=user_id)`, which
is what upstream replaced its own callers with (`routers/models.py:360`);
`has_permission_filter` treats the owner as always having access, so the set is
unchanged.

An AST sweep of the same file found no other call to a method that no longer
exists. Other fork-owned modules were not swept; the plane reaches them only
where a route drives them.

## PLANE-012: knowledge metadata reindex has no bound on its own duration

Status: **open**, intermittent, state-dependent. Surfaced 2026-09-10 as a 30s
client read timeout that aborted the drive and shapes passes:

    requests.exceptions.ReadTimeout: HTTPConnectionPool(host='127.0.0.1', port=8080):
    Read timed out. (read timeout=30)   # POST /api/v1/knowledge/metadata/reindex

`reindex_knowledge_base_metadata_embeddings` (`routers/knowledge.py:512`) deletes
the metadata collection and then loops over **every** knowledge base, awaiting one
external embedding call each, inside the request. Its own docstring shows the
author knew the loop makes N external calls — it deliberately avoids holding a DB
session for that reason — but nothing bounds how long the request itself takes.
With no knowledge bases it returns in 0.0s; driven after a seeding pass has
created many, it exceeds 30s. A tenant with a realistic number of knowledge bases
would exceed any client or proxy timeout, and there is no way to observe progress
or resume. Admin-only, so the exposure is availability, not access.

Not reproduced on every run: whether it fires depends on how many knowledge bases
exist when the drive pass reaches it, which varies with payload sampling and with
what earlier DELETE routes removed.

**It also shows a plane-level gap worth a separate decision:** a read timeout
currently aborts the whole pass, so one slow route blinds the coverage
measurement for every route after it. Recording a timeout as a measurement —
route not entered, surfaced in the tally like a 5xx — would be the conservative
behaviour, but it changes what the plane counts and deserves its own control and
review rather than a late edit.

## CI-003: the notifications surface was seeded but is gated off in production

Status: **fixed**. The v0.11.3 gate refresh declared
`POST /api/v1/notifications/targets` as a seed from the spec alone; identity
setup was blocked at the time, so it was never driven. Live, it answers 404 —
and so does the whole notifications surface, because
`_check_notifications_access` (`routers/notifications.py:34`) gates it on
`ui.enable_user_webhooks`, which `helm/open-webui-tenant/values.yaml:443` defaults
to `"false"` and no tenant overrides. The CI stack matching production is the
correct state, so the seed was the thing that was wrong; it is now recorded
`unseedable` with that reasoning.

Nothing was ever miscounted as covered: the 404 is the application's own
NOT_FOUND and `entered_the_handler` refuses 404 unconditionally. The cost was
that the failing seed aborted every dependent live pass — 54 errors from one
declaration.

## PLANE-013: a poisoned embedding configuration unloads the model, and restoring the values does not reload it

Status: **open**. Found by making the payload builder reach handlers it had
never entered; invisible while those routes answered 422.

After the seeding pass, `GET /api/v1/configs/export` reports
`rag.embedding_engine`, `rag.embedding_model`, `rag.openai.api_base_url` and
`openai.api_base_urls` as `null` — and the process has unloaded its embedding
function. Every route that needs an embedding then fails:

    POST /api/v1/memories/add -> 500
    open_webui.routers.knowledge:embed_knowledge_base_metadata -
      No embedding model is loaded. Set RAG_EMBEDDING_MODEL to a valid
      SentenceTransformer model name, or configure an external
      RAG_EMBEDDING_ENGINE (ollama, openai, azure_openai).

The part that makes it a finding rather than a consequence: **restoring the
configuration does not bring the model back.** Measured after a full run, with
later passes having restored the values, `configs/export` read entirely healthy
— `embedding_engine: "openai"`, `embedding_model: "text-embedding-3-small"`,
the correct stub URL and key — and `POST /api/v1/memories/add` still answered
500. A `docker restart` with that same configuration answered 200. Re-importing
the identical snapshot through `POST /api/v1/configs/import` also answered 200,
so the reload is reachable; it just does not happen on the path an operator
would take.

The operator-facing shape is worse than the harness one. An admin who saves a
bad connection configuration and then fixes it keeps a wedged instance until
the process restarts, while the configuration UI and `configs/export` report
that everything is correct. Nothing surfaces the difference between "configured"
and "loaded".

**A control separates this from the harness change that found it.** Running the
same seeding pass against unmodified `dev`, the configuration is nulled exactly
the same way — so the poisoning is PLANE-001 and predates this work — but
`POST /api/v1/memories/add` still answers **200**, because no payload ever
reached the handlers that act on the nulled values. The damage was latent. What
changed is that it now takes effect, and one failing seed then aborts every
dependent pass: `Seeding POST /api/v1/memories/add failed (HTTP 500)`, 15
errors.

**The guard claims success over this.** The seeding pass's own artefact records
`config_restore_verified: true` and `config_unverified: {}` alongside 11,274
PLANE-001 findings and 169 poisoned keys, `rag.embedding_engine` and
`rag.embedding_model` among them. `restore()` re-imports `before[key]` for every
changed key and raises if the resulting snapshot differs, so the claim is about
configuration values only. It cannot see derived runtime state, and nothing
currently says so. Until it can, `config_restore_verified` should not be read as
"the stack is as it was".

## CI-004: the CI stack image is older than `dev`, and `down -v` reverts the difference

Status: **open**, and it is a trap rather than a bug. The `open-webui-ci` image
predates PR #289 and PR #291 — nine application files differ from the tree,
`services/export/service.py` among them. Sessions have been closing that gap by
`docker cp`-ing application code into the *running container*, which works and
survives a restart, but **not** a `docker compose down -v`: recreating the
container restores the image's code, silently.

The failure it produces reads like a regression in whatever changed most
recently. A run on the recreated stack aborted with

    Data export failed for user <id>:
    'ModelsTable' object has no attribute 'get_models_by_user_id'

which is PLANE-011 exactly — the regression #289 fixed and this document
records as fixed. `seed_export` then timed out at 60 seconds, and one failing
seed aborted every dependent live pass: **54 errors**, the same shape and the
same count as CI-003.

The control already exists and is written down: verify a sha256 manifest of
`backend/**/*.py` on both sides before trusting a run. This is the instance
that shows why. Two cautions for whoever automates it — `docker cp` needs a
`docker restart` afterwards for the app process to load the new code, and
macOS `sort` and the container's `sort` collate `_` and `.` differently, so
compare under `LC_ALL=C` or the manifests differ by ordering alone and the
check cries wolf.

Rebuilding the image is the durable fix. It is deferred rather than dismissed:
this document already records two overnight builds OOM-killed here, so the
build wants the stack stopped and a session that can afford it.

## BLOCKER-001: live runs cannot set up identities on v0.11.3

Status: **resolved 2026-09-10**. Both halves are fixed and verified live; the
entry is kept because the reasoning is the durable record of why the client was
not softened.

`routers/users.py:976` calls `revoke_user_tokens` when an admin changes a user's
password. The identity helper repairs an identity by resetting its password
through `POST /api/v1/users/{id}/update`, so when the target was the driving
admin it destroyed the bearer making the request:

    AuthenticationError: Attack bearer identity was lost at
    POST /api/v1/users/<admin-id>/update: Invalid token

The client was behaving correctly: that response IS a genuine identity-loss
marker and it must stay one. Softening it would also soften genuinely losing an
identity mid-pass, which is the failure that reports full coverage of an
application never entered. The repair therefore belongs in the helper, which is
the only party that knows the revocation was deliberate.

**What the fix turned out to be — three parts, not one.**

1. *The driver repairs itself.* `signin_alias` now re-authenticates the admin
   in place (`AttackClient.reauthenticate`) after a reset that revoked its own
   bearer, so the restore of the stable email and every later repair run on a
   live session.

2. *Revocation is a whole-second marker, not a token list.* `revoked_at` is
   stored in seconds (`utils/auth.py:323`) and every token whose `iat` is at or
   before it is rejected (`utils/auth.py:275`); `iat` is whole seconds too
   (`utils/auth.py:237`). A password reset and the sign-in after it are a few
   hundred milliseconds apart, so a replacement session minted immediately is
   usually born revoked — the common case, not a rare race. Repair now waits out
   that second, for every identity. This was not in the original diagnosis and
   would have produced a fix that passed offline and failed live most runs.

3. *Repair only what is broken.* Resetting unconditionally revoked the previous
   pass's sessions, which are still open while that pass tears down:
   `AuthenticationError ... POST /api/tasks/stop/<id>: Invalid token`. Giving each
   pass a distinct jti was the defence against one pass signing another out, and
   per-user revocation goes straight through it. The helper now tries the standard
   credential first and resets only on a 400, so an untouched password is left
   alone. `AuthenticationError` carries the refusing status for that reason: a
   throttle or a 2FA challenge is not a credential a reset would fix.

Also: the private recovery JWT in `.cache` is itself revoked when the admin is
repaired, so the replacement is persisted. Without that, the next setup falls
back to the bootstrap path and spends the stable admin email's signin throttle.

**The order that kept the controls honest.** The revocation was modelled in the
`IdentityServer` double first, and its clock advances only when the code under
test sleeps, so waiting out the revoked second is a tested property rather than a
comment. The seven identity-repair controls then failed for the same reason the
live stack did, and the fix is what made them pass; removing either half of it,
or the recovery-token save, turns them red again. An eighth control now drives
two consecutive setups and requires both sets of sessions to survive.

**A wrecked stack is recoverable by hand.** A run interrupted between the alias
sign-in and the email restore leaves the admin stranded under
`attack-signin-<hex>@example.com` with the standard password and a revoked
recovery token, which `_recover_admin` cannot repair (it signs in as the stable
email). Sign in as the alias with `PASSWORD` and `POST /api/v1/users/{id}/update`
`{"email": "attack-admin@example.com"}` — the restore the interrupted repair
never finished. Resetting the stack works too, and destroys the config journal.
