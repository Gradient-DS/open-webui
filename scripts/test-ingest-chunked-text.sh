#!/usr/bin/env bash
#
# Test chunked_text ingest.
# Requires a provider configured with data_type = "chunked_text".
#
# Usage:
#   ./scripts/test-ingest-chunked-text.sh --api-key sk-xxxxx [--cleanup]
#

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_test-ingest-common.sh"
parse_args "$@"
init_api

echo "=== Ingest API Test: chunked_text ==="
echo "Base URL:      $BASE_URL"
echo "Collection ID: $COLLECTION_ID"

# --- 1. Ingest 2 chunked documents ---
echo ""
echo "--- 1. Ingesting 2 chunked_text documents ---"
INGEST_DATA=$(cat <<JSONEOF
{
  "collection": {
    "source_id": "$COLLECTION_ID",
    "name": "Test - Chunked Text",
    "description": "Automated test for chunked_text ingest",
    "data_type": "chunked_text",
    "language": "nl",
    "tags": ["test", "chunked-text"]
  },
  "documents": [
    {
      "source_id": "doc-history",
      "filename": "dutch-history.txt",
      "content_type": "text/plain",
      "chunks": [
        "De Gouden Eeuw was een periode in de Nederlandse geschiedenis die ruwweg samenviel met de 17e eeuw. In deze tijd was de Republiek der Zeven Verenigde Nederlanden een van de machtigste landen ter wereld.",
        "De VOC (Verenigde Oost-Indische Compagnie) werd opgericht in 1602 en was het eerste multinationale bedrijf ter wereld. Het had het monopolie op de handel met Azie.",
        "De Nederlandse kunst bloeide in de Gouden Eeuw. Schilders als Rembrandt, Vermeer en Frans Hals maakten wereldberoemde werken."
      ],
      "title": "Nederlandse Geschiedenis - Gouden Eeuw",
      "source_url": "https://example.com/gouden-eeuw",
      "language": "nl",
      "author": "Test Suite",
      "tags": ["geschiedenis", "nederland"]
    },
    {
      "source_id": "doc-recipes",
      "filename": "dutch-recipes.txt",
      "content_type": "text/plain",
      "chunks": [
        "Stamppot is een traditioneel Nederlands gerecht van gekookte en gestampte aardappelen gemengd met groenten zoals boerenkool, zuurkool of andijvie.",
        "Erwtensoep, ook wel snert genoemd, is een dikke soep van spliterwten met rookworst, selderij en prei. Het wordt traditioneel gegeten in de winter."
      ],
      "title": "Nederlandse Recepten",
      "source_url": "https://example.com/recepten",
      "language": "nl",
      "author": "Test Suite",
      "tags": ["recepten", "nederland"]
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
echo "Documents created (expected 2): $CREATED"

# --- 2. Idempotency ---
echo ""
echo "--- 2. Idempotency test (re-push same doc) ---"
IDEM_DATA=$(cat <<JSONEOF
{
  "collection": {
    "source_id": "$COLLECTION_ID",
    "name": "Test - Chunked Text",
    "data_type": "chunked_text"
  },
  "documents": [
    {
      "source_id": "doc-history",
      "filename": "dutch-history.txt",
      "chunks": [
        "De Gouden Eeuw was een periode in de Nederlandse geschiedenis die ruwweg samenviel met de 17e eeuw. In deze tijd was de Republiek der Zeven Verenigde Nederlanden een van de machtigste landen ter wereld.",
        "De VOC (Verenigde Oost-Indische Compagnie) werd opgericht in 1602 en was het eerste multinationale bedrijf ter wereld. Het had het monopolie op de handel met Azie.",
        "De Nederlandse kunst bloeide in de Gouden Eeuw. Schilders als Rembrandt, Vermeer en Frans Hals maakten wereldberoemde werken."
      ],
      "title": "Nederlandse Geschiedenis - Gouden Eeuw",
      "source_url": "https://example.com/gouden-eeuw"
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
echo "--- 3. Deleting single document (doc-recipes) ---"
RESPONSE=$(curl -s -w "\n%{http_code}" -X DELETE "$API/collections/$COLLECTION_ID/documents/doc-recipes" \
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
