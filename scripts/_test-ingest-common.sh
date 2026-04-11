# Common helpers for ingest test scripts. Source this, don't run directly.

set -euo pipefail

API_KEY=""
BASE_URL="http://localhost:8080"
CLEANUP=false
COLLECTION_ID="test-collection-$(date +%s)"

parse_args() {
  while [[ $# -gt 0 ]]; do
    case $1 in
      --api-key|-k)       API_KEY="$2"; shift 2 ;;
      --base-url|-u)      BASE_URL="$2"; shift 2 ;;
      --cleanup|-c)       CLEANUP=true; shift ;;
      --collection-id)    COLLECTION_ID="$2"; shift 2 ;;
      *) echo "Unknown option: $1"; exit 1 ;;
    esac
  done

  if [[ -z "$API_KEY" ]]; then
    echo "Error: --api-key is required"
    echo "Usage: $0 --api-key sk-xxxxx [--base-url http://localhost:8080] [--cleanup]"
    exit 1
  fi
}

API=""
PASS=0
FAIL=0

init_api() {
  API="$BASE_URL/api/v1/integrations"
}

assert_http() {
  local label="$1" expected="$2" actual="$3" body="$4"
  echo "HTTP $actual"
  echo "$body" | python3 -m json.tool 2>/dev/null || echo "$body"
  echo ""
  if [[ "$actual" != "$expected" ]]; then
    echo "FAIL [$label]: Expected HTTP $expected, got $actual"
    FAIL=$((FAIL + 1))
    return 1
  fi
  PASS=$((PASS + 1))
  return 0
}

cleanup_collection() {
  local cid="$1"
  echo "  Cleaning up collection $cid ..."
  curl -s -o /dev/null -X DELETE "$API/collections/$cid" \
    -H "Authorization: Bearer $API_KEY" || true
}

print_results() {
  echo ""
  echo "=========================================="
  echo " Results: $PASS passed, $FAIL failed"
  echo "=========================================="
  if [[ "$FAIL" -gt 0 ]]; then
    exit 1
  fi
}
