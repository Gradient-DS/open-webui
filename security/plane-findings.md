RUN-TAG: owui-phase7a-record-5xx-findings

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

## PLANE-003: Stored note content breaks subsequent note listings

Status: **open, to be fixed on dev**. The live reviewer reported
`GET /api/v1/notes/` among the failures on both 2026-09-08 passes.

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
  as HTTP 500. The reviewer's cited call at `197` is the search listing;
  the root listing calls the same helper at `99` in this checkout.

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
```

Expected: creation persists the note; the subsequent GET returns 500 with the
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

## CI-001: Missing stub DELETE produces upstream 501 responses

Scope: **CI stack gap, not an application finding**. Status: **fixed in the
stub; live proxy confirmation pending**. This accounts for the remaining two
reported routes together, subject to the Ollama attribution limit below:

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
  produces a 501 here, with an application JSON error envelope. **The reviewer
  did not supply this route's exact status/body or traceback**, so this is a
  source-established CI cause, not proof that every observed Ollama 5xx had
  that cause. If it remains 5xx after the stub restart, retain it as unresolved
  evidence and diagnose that response on the live stack. The unchecked index
  at `784` is not evidence of an out-of-range index in these seeded runs.

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

Restart the stub process to load the bind-mounted change:
`docker compose -f docker-compose.ci.yaml restart stub`.
Terminal proxy should now return 200 JSON; indexed Ollama delete should return
200 (`true`) if its remaining application/event path succeeds. No 5xx exception
or accepted-finding entry was added to the plane gate or expectations.
