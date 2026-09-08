RUN-TAG: owui-phase7a-live-hardening

## Offline results and the two reported regex failures

Before changes, the attack suite passed **234 tests**, with **49 live tests
skipped**. After the recovery and signin changes it passes **250 tests**, with
the same **49 skipped**. This was checked with both Python 3.12 and the reviewer's
Python 3.11 interpreter and pinned helper packages. The complete offline security
suite passes **617 tests**, with **49 skipped** and 34 existing warnings, including
fresh OpenAPI comparison and static-asset protection. Ruff and diff checks pass.

The two regex failures reported in FINDINGS.md could not be reproduced offline,
including on the starting commit `bf13317a6`. In particular, both roles in
`test_real_model_checks_require_a_row_and_read_access_even_for_admin` pass. These
tests execute the actual `utils/models.py` functions: missing model rows and
missing read grants still raise `Model not found`; granting read access admits
the registered model. There is no evidence for changing those expectations.

The signin tests did have a separate, reproducible isolation flaw: stable email
buckets remain exhausted after the throttle test. A later wrong-password or 2FA
check can receive 429 before reaching the behavior it asserts. `signin_alias`
now extends the existing setup repair mechanism to these checks, restores the
stable email in `finally`, and leaves passwords, IDs and ownership intact. The
HTTP 400 and 2FA regex expectations remain unchanged. Offline tests exhaust all
stable email buckets before three repeated setups and wrong-password checks,
and verify that a fresh alias itself still throttles on attempt 16.

**Item 4 remains unconfirmed:** the supplied report and retained reviewer output
contain only aggregate regex-failure counts, not the failing test names or actual
exception strings. It would be speculation to attribute both failures to signin
or model registration. No regex was loosened and no application fix was made.
The live commands below retain full tracebacks and JUnit results so any remaining
failure can be diagnosed from its actual exception. Return those artifacts if
the failures recur.

## Reviewer commands

Run from `/Users/lexlubbers/Code/.worktrees/owui-attack-plane`. These commands use
the existing reviewer interpreter and pinned packages already present locally.
They do not rebuild, reset or restart the live stack: setup must recover its
previously poisoned state. Keep `.cache/attack/` between runs so the private admin
recovery JWT and identity IDs survive.

```sh
review_python=/Users/lexlubbers/Code/soev/open-webui/.venv/bin/python
review_helpers=/private/tmp/owui-phase7a-deps.3XA58n
review_extras=/private/tmp/owui-phase4b-python-deps
mkdir -p .cache/phase7a-live-hardening

# Full offline security suite; local application imports use an isolated DB.
env -u ATTACK_BASE_URL \
  DATABASE_TYPE= DATABASE_HOST= DATABASE_PORT= DATABASE_NAME= DATABASE_USER= DATABASE_PASSWORD= \
  DATABASE_URL=sqlite:///.cache/phase7a-live-hardening/webui.db \
  DATA_DIR=.cache/phase7a-live-hardening VECTOR_DB=chroma CHROMA_HTTP_HOST= \
  OFFLINE_MODE=true WEBUI_SECRET_KEY=offline-plane-test \
  PYTHONPATH="backend:$review_helpers:$review_extras" \
  "$review_python" -m pytest backend/open_webui/test/security -q --tb=long

# Two consecutive complete attack-suite runs, without clearing buckets or DB.
for review_run in 1 2; do
  env ATTACK_BASE_URL=http://127.0.0.1:8080 ATTACK_FULL_CORPUS=0 \
    ROUTE_HITS_PATH="$PWD/.cache/phase7a-live-hardening/hits-$review_run.json" \
    PYTHONPATH="backend:$review_helpers:$review_extras" \
    "$review_python" -m pytest backend/open_webui/test/security/attack \
      -q -s --tb=long \
      --junitxml=".cache/phase7a-live-hardening/results-$review_run.xml" \
      > ".cache/phase7a-live-hardening/live-$review_run.log" 2>&1
  review_status=$?
  cat ".cache/phase7a-live-hardening/live-$review_run.log"
  if [ "$review_status" -ne 0 ]; then exit "$review_status"; fi
done
```

Prediction: offline **617 passed, 49 skipped**; each live attack run **299 passed,
0 skipped, 0 errors**, provided no additional live findings surface. The live
prediction is not a result: this sandbox cannot reach the running app, and item 4
still needs the unfiltered live run above. For a full security run with the live
stack enabled, the corresponding prediction is **666 passed**.

Coverage expectations are exact inventory counts, unchanged from the starting
commit: **631 operations**, **240 writable routes**, **2,591 string fields** and
**243 resolved path/parameter pairs**. Seeding must record requests for **238
routes**, with **2 explicitly unseedable**; drive must record requests for **617
routes**, with **14 explicitly unseedable**. Neither numeric recovery nor signin
setup contributes route hits. Actual handler-entry counts depend on live
responses and must be compared with the reviewer's previous artifact; they
cannot be inferred from the inventory or promised offline.

In each hits artifact, `POST /api/v1/retrieval/config/update` must still occur in
the seeding statuses and both config write routes in drive statuses. If a write
persists the poison, `passes.seeding.config_findings` records `PLANE-001`, the
route, key, observed payload and restored value. A subsequent fixture pass must
upload successfully. Setup prints the original value when repairing poison
already present before the run. The underlying application finding remains open
in `security/plane-findings.md`.
