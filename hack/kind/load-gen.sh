#!/usr/bin/env bash
# Load generator + log scanner for thoughts/shared/research/2026-04-20-redis-ha-loop-bug-and-kind-repro.md reproduction.
#
# Hits /api/chat/completions concurrently across both replicas. Afterward
# greps pod logs for "attached to a different loop" or "Event loop is
# closed". Non-zero exit on any match.

set -euo pipefail

CLUSTER="${CLUSTER:-soev-ha}"
NAMESPACE="${NAMESPACE:-soev-local}"
HOST="${HOST:-soev.local}"
PORT="${PORT:-8080}"
CONCURRENCY="${CONCURRENCY:-50}"
REQUESTS="${REQUESTS:-500}"
TOKEN="${TOKEN:-}"

if [[ -z "$TOKEN" ]]; then
  echo "TOKEN is unset. Log into the UI at http://$HOST:$PORT, copy your JWT from" >&2
  echo "localStorage.token, and re-run with TOKEN=... make -C hack/kind repro" >&2
  exit 2
fi

if ! command -v hey >/dev/null 2>&1; then
  echo "Missing 'hey' — brew install hey" >&2
  exit 2
fi

echo "==> Firing $REQUESTS requests @ concurrency $CONCURRENCY …"
hey -n "$REQUESTS" -c "$CONCURRENCY" -m POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"ping"}],"stream":false}' \
  "http://$HOST:$PORT/api/chat/completions" || true

echo ""
echo "==> Scanning pod logs for bug signatures…"
if kubectl --context "kind-$CLUSTER" -n "$NAMESPACE" logs \
    -l app.kubernetes.io/name=open-webui-tenant,app.kubernetes.io/component=open-webui \
    --all-containers=true --tail=2000 \
    | grep -E "attached to a different loop|Event loop is closed" ; then
  echo ""
  echo "FAIL: loop-binding errors present in pod logs." >&2
  exit 1
fi

echo "OK: no loop-binding errors observed."
