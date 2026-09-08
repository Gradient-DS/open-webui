RUN-TAG: owui-phase7a-config-poisoning-class

## Measured offline results

Python 3.11 with the reviewer's existing helper packages: **258 passed, 49 skipped**
for `backend/open_webui/test/security/attack`; **625 passed, 49 skipped** for the
complete offline security suite. The latter includes fresh OpenAPI comparison
and protection of `backend/open_webui/static/`. Ruff and diff checks pass.
The clean-default helper separately produced valid JSON with **471 defaults**
from this checkout under the offline environment; the running image's defaults
may differ. No live request was attempted: the sandbox blocks loopback.

These supersede the previous **299 passed** live prediction. The attack suite
now collects **307 tests**: 258 offline tests plus 49 stack-dependent tests. A
live run's exact passed/failed/error counts are **unknown**, for either starting
state. In particular, seeding/drive have independent assertions against 5xx;
retaining a config write that persists then returns 500 can correctly fail such
an assertion even after successful recovery. Further application failures,
authentication, vector/stub availability and fixture prerequisites also need the
stack. A fully green run would have 307 passed; that is a ceiling, not a forecast.
The full security suite analogously collects 674 tests with live checks enabled.

The tests cover simultaneous poisoning of the integer upload limit, embedding
engine and an unlisted nested URL key; successful and 500 writes; disconnected
responses after persistence; repeated passes; failed restore with a retained
private journal; next-setup replay; malformed exports; missing/dirty baselines;
missing/new keys; boolean versus integer verification; and failure bodies under
HTTP 200. Unrelated plane unit tests isolate the config guard; the dedicated
configuration tests run the actual guard through `seed_every_writable_field`.

The earlier two reported regex failures remain unconfirmed. No regex assertion
was loosened. Full tracebacks and JUnit output below retain any recurrence.

## Commands shared by both live scenarios

Run from `/Users/lexlubbers/Code/.worktrees/owui-attack-plane`. Use the same running
image, sealed services, interpreter and corpus for both scenarios. Keep the
identity recovery cache and configuration journal between repeated runs against
the same stack. Run serially. The existing CI container is `open-webui-ci`.

```sh
review_python=/Users/lexlubbers/Code/soev/open-webui/.venv/bin/python
review_helpers=/private/tmp/owui-phase7a-deps.3XA58n
review_extras=/private/tmp/owui-phase4b-python-deps
review_output="$PWD/.cache/phase7a-config-class"
mkdir -p "$review_output"

# Offline: expect 625 passed, 49 skipped.
env -u ATTACK_BASE_URL \
  DATABASE_TYPE= DATABASE_HOST= DATABASE_PORT= DATABASE_NAME= DATABASE_USER= DATABASE_PASSWORD= \
  DATABASE_URL=sqlite:///.cache/phase7a-config-class/webui.db \
  DATA_DIR=.cache/phase7a-config-class VECTOR_DB=chroma CHROMA_HTTP_HOST= \
  OFFLINE_MODE=true WEBUI_SECRET_KEY=offline-plane-test \
  PYTHONPATH="backend:$review_helpers:$review_extras" \
  "$review_python" -m pytest backend/open_webui/test/security -q --tb=long

run_config_review() {
  review_case="$1"
  mkdir -p "$review_output/$review_case"
  # New process, same container environment; does NOT export the poisoned
  # server's mutable defaults, restart it, or reset its DB. Static writes are
  # redirected to a temporary directory and migrations are disabled.
  (umask 077
   docker exec -i open-webui-ci python - < cicd/config_baseline.py \
     > "$review_output/$review_case/baseline.json") || return $?

  review_failed=0
  for review_run in 1 2; do
    env ATTACK_BASE_URL=http://127.0.0.1:8080 ATTACK_FULL_CORPUS=0 \
      ATTACK_CONFIG_BASELINE="$review_output/$review_case/baseline.json" \
      ATTACK_CONFIG_STATE_DIR="$PWD/.cache/attack" \
      ROUTE_HITS_PATH="$review_output/$review_case/hits-$review_run.json" \
      PYTHONPATH="backend:$review_helpers:$review_extras" \
      "$review_python" -m pytest backend/open_webui/test/security/attack \
        -q -s --tb=long \
        --junitxml="$review_output/$review_case/results-$review_run.xml" \
        > "$review_output/$review_case/live-$review_run.log" 2>&1
    review_status=$?
    cat "$review_output/$review_case/live-$review_run.log"
    if [ "$review_status" -ne 0 ]; then review_failed=1; fi
    # Continue to run 2 even if a finding made run 1's assertions fail.
  done
  "$review_python" cicd/config_findings.py \
    "$review_output/$review_case/hits-1.json" \
    "$review_output/$review_case/hits-2.json" \
    > "$review_output/$review_case/config-keys.md" || review_failed=1
  return "$review_failed"
}
```

## Previously poisoned stack

Use the stack left running by the earlier reviewer pass. **Do not recreate or
restart it before this case.** The helper is sent over stdin so even an older
container can use it without an image rebuild or an existing cicd bind mount.

```sh
run_config_review poisoned
```

Expected startup behavior: before the first fixture request, `resolve_parameters`
replays any pending snapshot, then scans the entire export recursively for
corpus values. Old poison without a journal is restored from clean environment
defaults only for contaminated keys. The log must report at least the embedding
engine if it is still poisoned, plus whichever other keys are still contaminated.
There is no known exact repair count. Valid pre-existing values are preserved.
If the baseline lacks a poisoned key, setup fails explicitly before fixtures.
It does not choose a guessed engine, URL or enum.

The next integration seed must have `created: 1`, `errors: 0` and a real saved
attachment, assuming healthy underlying services. The upload seed must return a
real file ID. The previous approximately 40 errors caused by that single
poisoned seed should disappear; the total number of live failures cannot be
predicted offline. A second run must again get past these fixtures.

## Pristine stack

Run this **after** the poisoned case, using a newly created disposable stack.
If the current stack is the disposable compose stack launched from this worktree,
the following explicitly removes its data to create the pristine case. Do not
run it before retaining the poisoned-case artifacts above. Match any `-p` project
argument used when creating that stack on both compose commands.

```sh
docker compose -f docker-compose.ci.yaml down -v
# Preserve the old instance's private identities/journal; a replacement instance
# at the same URL must start with its own recovery state.
if [ -d .cache/attack ]; then
  mv .cache/attack "$review_output/poisoned/attack-state-$(date +%s)"
fi
docker compose -f docker-compose.ci.yaml up -d --build --wait
run_config_review pristine
```

Expected startup repair count: **0 corpus-contaminated keys** on the pristine
instance. The first and second run must meet the same fixture and restoration
checks as the poisoned case. Do not reuse a pending journal from the destroyed
instance. The image build can contain different application fixes than the older
running image; record image IDs if comparing their findings. Exact live test
outcomes and reached-key counts remain unknown on either image.

## Artifact checks and complete key list

Inventory is unchanged: **631 operations**, **240 writable routes**, **2,591
string fields**, **243 resolved path/parameter pairs**. If each pass completes,
seeding must show **238 driven routes + 2 explicitly unseedable**, and drive
**617 driven + 14 explicitly unseedable**. Handler-entry counts depend on actual
HTTP responses and cannot be predicted from these inventory counts.

Both passes must have `config_restore_verified: true`. `config_keys` enumerates
all changed keys, including optional/default collateral writes. `corpus_config_keys`
is the recognized corpus-bearing subset (probe, exact corpus or normalized URL target); `config_findings` retains route,
observed value, original target and key-presence flags, including changes preceding
500s. An empty final diff is expected after recovery; the intermediate diffs are
retained so it cannot erase the finding. Config recovery calls do not inflate
statuses or hits. Ingest's positive-error 2xx bodies are separately in
`body_failures`; the integration fixture continues to assert actual creation.

```sh
"$review_python" - "$review_output" <<'PY'
import json
import sys
from pathlib import Path
for path in sorted(Path(sys.argv[1]).glob('*/hits-*.json')):
    data = json.loads(path.read_text())
    for name, (driven, skipped) in {'seeding': (238, 2), 'drive': (617, 14)}.items():
        tally = data['passes'][name]
        assert tally['config_restore_verified'] is True, (path, name)
        assert len(tally['statuses']) == driven, (path, name)
        assert len(tally['skipped']) == skipped, (path, name)
        print(path, name, 'driven', driven, 'skipped', skipped,
              'reached', len(tally['entered']), 'corpus keys', tally['corpus_config_keys'])
    assert 'POST /api/v1/retrieval/config/update' in data['passes']['seeding']['statuses']
    assert 'POST /api/v1/retrieval/embedding/update' in data['passes']['seeding']['statuses']
    assert 'POST /api/v1/configs/import' in data['passes']['drive']['statuses']
PY

# Insert the complete changed-key union under PLANE-001, preserving PLANE-002.
"$review_python" cicd/config_findings.py \
  "$review_output/poisoned/hits-1.json" "$review_output/poisoned/hits-2.json" \
  "$review_output/pristine/hits-1.json" "$review_output/pristine/hits-2.json" \
  --findings security/plane-findings.md
```

Only two keys currently have supplied live evidence: `rag.file.max_size` and
`rag.embedding_engine`. The report lists every changed key, including collateral and transformed values, without
inventing effects: rows beyond those two explicitly require consumer review.
Retain the full diffs and logs for that review. A complete *live* list cannot be
committed before the reviewer produces it; this document does not present the
mock server's third, hypothetical key as an application finding.
