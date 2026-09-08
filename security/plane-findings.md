RUN-TAG: owui-phase7a-config-poisoning-class

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
