#!/usr/bin/env bash
#
# Test parsed_text ingest.
# Requires a provider configured with data_type = "parsed_text" (or no data_type, since it's the default).
#
# Usage:
#   ./scripts/test-ingest-parsed-text.sh --api-key sk-xxxxx [--cleanup]
#

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_test-ingest-common.sh"
parse_args "$@"
init_api

echo "=== Ingest API Test: parsed_text ==="
echo "Base URL:      $BASE_URL"
echo "Collection ID: $COLLECTION_ID"

# --- 1. Ingest 3 documents ---
echo ""
echo "--- 1. Ingesting 3 parsed_text documents ---"
INGEST_DATA=$(cat <<JSONEOF
{
  "collection": {
    "source_id": "$COLLECTION_ID",
    "name": "Test - Parsed Text",
    "description": "Automated test for parsed_text ingest",
    "data_type": "parsed_text",
    "language": "nl",
    "tags": ["test", "parsed-text"]
  },
  "documents": [
    {
      "source_id": "doc-amsterdam",
      "filename": "amsterdam-overview.txt",
      "content_type": "text/plain",
      "text": "Amsterdam is de hoofdstad en grootste stad van Nederland. De stad ligt in de provincie Noord-Holland aan de monding van de Amstel en het IJ.",
      "title": "Amsterdam - Overzicht",
      "source_url": "https://example.com/amsterdam",
      "language": "nl",
      "author": "Test Suite",
      "tags": ["stad", "nederland"]
    },
    {
      "source_id": "doc-ai-basics",
      "filename": "ai-fundamentals.txt",
      "content_type": "text/plain",
      "text": "Artificial Intelligence (AI) is een breed vakgebied binnen de informatica. Machine learning stelt computers in staat te leren van data zonder expliciet geprogrammeerd te worden.",
      "title": "AI Grondbeginselen",
      "source_url": "https://example.com/ai-basics",
      "language": "nl",
      "author": "Test Suite",
      "tags": ["ai", "technologie"]
    },
    {
      "source_id": "doc-climate",
      "filename": "klimaatverandering.txt",
      "content_type": "text/plain",
      "text": "Klimaatverandering is een van de grootste uitdagingen van onze tijd. De gemiddelde temperatuur op aarde is sinds het pre-industriele tijdperk met ongeveer 1,1 graden Celsius gestegen.",
      "title": "Klimaatverandering",
      "source_url": "https://example.com/klimaat",
      "language": "nl",
      "author": "Test Suite",
      "tags": ["klimaat", "milieu"]
    }
  ]
}
JSONEOF
)

RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "$API/ingest" \
  -H "Authorization: Bearer $API_KEY" \
  -F "data=$INGEST_DATA")
HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | sed '$d')
assert_http "ingest" "200" "$HTTP_CODE" "$BODY" || { print_results; exit 1; }

CREATED=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin)['created'])" 2>/dev/null || echo "0")
echo "Documents created: $CREATED"

# --- 2. Idempotency ---
echo ""
echo "--- 2. Idempotency test (re-push same doc) ---"
IDEM_DATA=$(cat <<JSONEOF
{
  "collection": {
    "source_id": "$COLLECTION_ID",
    "name": "Test - Parsed Text",
    "data_type": "parsed_text"
  },
  "documents": [
    {
      "source_id": "doc-amsterdam",
      "filename": "amsterdam-overview.txt",
      "text": "Amsterdam is de hoofdstad en grootste stad van Nederland. De stad ligt in de provincie Noord-Holland aan de monding van de Amstel en het IJ.",
      "title": "Amsterdam - Overzicht",
      "source_url": "https://example.com/amsterdam"
    }
  ]
}
JSONEOF
)

RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "$API/ingest" \
  -H "Authorization: Bearer $API_KEY" \
  -F "data=$IDEM_DATA")
HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | sed '$d')
assert_http "idempotency" "200" "$HTTP_CODE" "$BODY" || { print_results; exit 1; }

UPDATED=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin)['updated'])" 2>/dev/null || echo "0")
echo "Documents updated (expected 1): $UPDATED"

# --- 3. Delete single doc ---
echo ""
echo "--- 3. Deleting single document (doc-climate) ---"
RESPONSE=$(curl -s -w "\n%{http_code}" -X DELETE "$API/collections/$COLLECTION_ID/documents/doc-climate" \
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
