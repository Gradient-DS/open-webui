# Authorization and model-path review

RUN-TAG: owui-phase7b-authorization-and-model-passes

The additions are `attack/crossuser.py`, `attack/model_path.py`, their offline
and live tests, and `attack/authorization_inventory.py`. Existing backend files,
static assets, application behavior, and expectations are unchanged.

`crossuser` calls `seeds.resolve_parameters` itself with a separate copy of the
seed surface, a unique marker in declarative fixtures, and the procedural
seeders' separate token. It never accepts the suite's parameter dictionary.
The seed declaration and aliases select the actual owner: admin for most rows,
ordinary caller for integration rows, and the disposable seed user for exports.
Each second-account request precedes its owner control. Reads precede writes;
child removals precede parent removals; destructive routes are last and use fresh
throwaway accounts of the appropriate role. Full response bodies are checked
for markers even on refusal, and successful responses are checked for seeded
object IDs and matching owner objects. A failed owner control is reported as
unproven isolation, including two 404s. It is not treated as a passing check.

The source inventory resolves all 631 registered operations without importing
the application, including named method constants, path converters, duplicate
function names, conditional registrations, and admin dependencies. The selected
authorization surface is 413 operations: the union of 264 parameterized operations
and 209 admin-only operations. Fourteen operations have declared unseedable
parameters. Inventory counts are not handler-entry claims.

`model_path` selects the chat route and eight task-completion routes, covering
433 derived string fields. In default sampling mode it sends 460 requests:
structural controls, content probes preserving the real model selector,
all-fields probes, and individual-field probes. Both modules use the shared
deterministic sampling; `ATTACK_FULL_CORPUS=1` expands the corpus. The modules
create separate tallies and use the existing configuration snapshot/restore.
Each has its own 5xx assertion, separate from shapes and query.

Model responses have a **5-second per-chunk timeout** and **60-second total
deadline**, including waiting for headers. Connection timeout is 10 seconds.
The serial POSIX/main-thread runner uses an interrupting timer so continued
trickles cannot evade the wall deadline. It restores the previous signal handler
and refuses to overwrite an existing alarm. SSE must dispatch a terminal event
and reach clean EOF; a finish-reason delta, terminal-looking substring, or a
200 status alone does not pass. Reading continues after the terminal event.
Partial bytes, returned statuses, generator errors and timeouts remain visible.
Ordinary JSON task responses are recorded separately; disabled-task JSON does
not satisfy a model-output positive control.

Verification on this checkout: **702 passed, 61 skipped** for the complete
offline security suite. The two new test files each have **25 passed, 4 skipped**
(combined **50 passed, 8 skipped**). Scoped Ruff formatting/lint checks passed.
`python -m bandit -r backend/ -ll --confidence-level=medium` exited 0 and reported
**No issues identified**, with no skipped files. Existing suppression-comment
warnings remain. No new source execution or Bandit suppressions were needed.

No live requests were attempted because the sandbox blocks loopback. There are
no newly observed application 500s to add to `plane-findings.md`. Offline mocked
500s test the gates; they are not application findings. Exact live pass/fail
counts, semantic positive-control success and handler-entry counts remain
unknown. Existing open findings can keep the live suite red. Missing positive
controls, authorization disclosures, stream truncation and 5xx are independent
failures; do not infer correctness from the number of failed assertions.

Run these commands from the worktree. The interpreter and helper directories
below are the existing reviewer environment used for offline verification.

```sh
cd /Users/lexlubbers/Code/.worktrees/owui-attack-plane
review_python=/Users/lexlubbers/Code/soev/open-webui/.venv/bin/python
review_helpers=/private/tmp/owui-phase7a-deps.3XA58n
review_extras=/private/tmp/owui-phase4b-python-deps
review_output="$PWD/.cache/phase7b-review"
mkdir -p "$review_output"

# Expect 702 passed, 61 skipped.
env -u ATTACK_BASE_URL \
  DATABASE_TYPE= DATABASE_HOST= DATABASE_PORT= DATABASE_NAME= DATABASE_USER= DATABASE_PASSWORD= \
  DATABASE_URL=sqlite:///.cache/phase7b-offline/webui.db \
  DATA_DIR=.cache/phase7b-offline VECTOR_DB=chroma CHROMA_HTTP_HOST= \
  OFFLINE_MODE=true WEBUI_SECRET_KEY=offline-plane-test \
  PYTHONPATH="backend:$review_helpers:$review_extras" \
  "$review_python" -m pytest backend/open_webui/test/security -q --tb=long

# Bandit was installed locally in this ignored directory. Expect exit 0 and
# "No issues identified" at the requested severity/confidence thresholds.
env PYTHONPATH="$PWD/.cache/phase7b-deps" \
  "$review_python" -m bandit -r backend/ -ll --confidence-level=medium

# Capture clean defaults from the running container's environment, not its
# mutable configuration export. Keep the existing recovery state across runs.
(umask 077
 docker exec -i open-webui-ci python - < cicd/config_baseline.py \
   > "$review_output/baseline.json")

# Run serially, twice on the same sealed stack. These commands enable the eight
# new live assertions; all 58 tests in these files should execute. A completely
# green run would be 58 passed, but its actual live outcome is not known offline.
for review_run in 1 2; do
  env ATTACK_BASE_URL=http://127.0.0.1:8080 ATTACK_FULL_CORPUS=0 \
    ATTACK_CONFIG_BASELINE="$review_output/baseline.json" \
    ATTACK_CONFIG_STATE_DIR="$PWD/.cache/attack" \
    ROUTE_HITS_PATH="$review_output/hits-$review_run.json" \
    PYTHONPATH="backend:$review_helpers:$review_extras" \
    "$review_python" -m pytest \
      backend/open_webui/test/security/attack/test_crossuser.py \
      backend/open_webui/test/security/attack/test_model_path.py \
      -q -s --tb=long --junitxml="$review_output/results-$review_run.xml" \
      > "$review_output/live-$review_run.log" 2>&1
  cat "$review_output/live-$review_run.log"
done
```

Repeat the live command with `ATTACK_FULL_CORPUS=1` for full-corpus coverage.
Use `backend/open_webui/test/security/attack` as the pytest target to run all
passes together. The artifacts must contain separate `crossuser` and
`model_path` entries, `config_restore_verified: true`, actual status histograms,
and pass-local handler entries. Model-response details and owner-control gaps
also appear in JUnit output. Skipped seed reasons must remain explicit. An HTTP
200 with a stream failure must still appear in `body_failures`.

For each newly observed unhandled 500, capture the request and container
traceback, locate the mechanism at `backend/open_webui/<file>:<line>`, and append
its reproduction to `security/plane-findings.md` with status
`open, to be fixed on dev`. Do not classify findings, change application code,
or modify expectations while recording this review.
