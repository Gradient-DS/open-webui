#!/usr/bin/env bash
#
# Test full_documents ingest.
# Requires a provider configured with data_type = "full_documents".
#
# Usage:
#   ./scripts/test-ingest-full-documents.sh --api-key sk-xxxxx [--cleanup]
#

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_test-ingest-common.sh"
parse_args "$@"
init_api

echo "=== Ingest API Test: full_documents ==="
echo "Base URL:      $BASE_URL"
echo "Collection ID: $COLLECTION_ID"

# Create temp test files
TMPDIR=$(mktemp -d)
trap "rm -rf $TMPDIR" EXIT

cat > "$TMPDIR/watermanagement.txt" <<'TXTEOF'
Water Management in the Netherlands

The Netherlands has a long and storied history of water management, dating back to the Middle Ages.
The country's relationship with water is fundamental to its identity — roughly 26% of the Netherlands
lies below sea level, and 59% of the land is susceptible to flooding.

The Dutch Delta Works, completed in 1997, is one of the most impressive hydraulic engineering projects
in the world. It consists of a series of dams, sluices, locks, dykes, and storm surge barriers.
The Maeslantkering, one of the largest moving structures on Earth, protects the port of Rotterdam
from storm surges.

Modern water management in the Netherlands involves a complex system of water boards (waterschappen),
which are among the oldest democratic institutions in the country, some dating back to the 13th century.
TXTEOF

cat > "$TMPDIR/cycling-culture.txt" <<'TXTEOF'
Cycling Culture in the Netherlands

The Netherlands is world-renowned for its cycling culture. With over 35,000 kilometers of dedicated
cycling paths, the country has more bikes than people — approximately 23 million bicycles for a
population of 17.5 million.

Dutch urban planning has prioritized cycling infrastructure since the 1970s, following the "Stop de
Kindermoord" (Stop the Child Murder) movement that protested the rising number of traffic deaths.
This led to a fundamental shift in how cities were designed, with separated bike lanes, traffic
calming measures, and bicycle-first intersections becoming standard.

The city of Utrecht houses the world's largest bicycle parking facility, with space for over
12,500 bikes at the central train station.
TXTEOF

# --- 1. Ingest 2 full documents ---
echo ""
echo "--- 1. Ingesting 2 full_documents ---"
INGEST_DATA=$(cat <<JSONEOF
{
  "collection": {
    "source_id": "$COLLECTION_ID",
    "name": "Test - Full Documents",
    "description": "Automated test for full_documents ingest",
    "data_type": "full_documents",
    "language": "en",
    "tags": ["test", "full-documents"]
  },
  "documents": [
    {
      "source_id": "doc-water",
      "filename": "watermanagement.txt",
      "content_type": "text/plain",
      "title": "Water Management in NL",
      "source_url": "https://example.com/water",
      "language": "en",
      "author": "Test Suite",
      "tags": ["water", "engineering"]
    },
    {
      "source_id": "doc-cycling",
      "filename": "cycling-culture.txt",
      "content_type": "text/plain",
      "title": "Dutch Cycling Culture",
      "source_url": "https://example.com/cycling",
      "language": "en",
      "author": "Test Suite",
      "tags": ["cycling", "culture"]
    }
  ]
}
JSONEOF
)

RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "$API/ingest" \
  -H "Authorization: Bearer $API_KEY" \
  -F "data=$INGEST_DATA" \
  -F "files=@$TMPDIR/watermanagement.txt" \
  -F "files=@$TMPDIR/cycling-culture.txt")
HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | sed '$d')
assert_http "ingest" "200" "$HTTP_CODE" "$BODY" || { print_results; exit 1; }

CREATED=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin)['created'])" 2>/dev/null || echo "0")
echo "Documents created (expected 2): $CREATED"

# --- 2. Idempotency ---
echo ""
echo "--- 2. Idempotency test (re-push same doc) ---"
IDEM_DATA=$(cat <<JSONEOF
{
  "collection": {
    "source_id": "$COLLECTION_ID",
    "name": "Test - Full Documents",
    "data_type": "full_documents"
  },
  "documents": [
    {
      "source_id": "doc-water",
      "filename": "watermanagement.txt",
      "content_type": "text/plain",
      "title": "Water Management in NL",
      "source_url": "https://example.com/water"
    }
  ]
}
JSONEOF
)

RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "$API/ingest" \
  -H "Authorization: Bearer $API_KEY" \
  -F "data=$IDEM_DATA" \
  -F "files=@$TMPDIR/watermanagement.txt")
HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | sed '$d')
assert_http "idempotency" "200" "$HTTP_CODE" "$BODY" || { print_results; exit 1; }

UPDATED=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin)['updated'])" 2>/dev/null || echo "0")
echo "Documents updated (expected 1): $UPDATED"

# --- 3. Delete single doc ---
echo ""
echo "--- 3. Deleting single document (doc-cycling) ---"
RESPONSE=$(curl -s -w "\n%{http_code}" -X DELETE "$API/collections/$COLLECTION_ID/documents/doc-cycling" \
  -H "Authorization: Bearer $API_KEY")
HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | sed '$d')
assert_http "delete doc" "200" "$HTTP_CODE" "$BODY"

# --- 4. Cleanup ---
if [[ "$CLEANUP" == true ]]; then
  echo ""
  echo "--- 4. Cleaning up collection ---"
  cleanup_collection "$COLLECTION_ID"
fi

print_results
