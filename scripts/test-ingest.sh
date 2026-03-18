#!/usr/bin/env bash
#
# Test scripts for the ingest API (multipart/form-data).
#
# Each data_type requires a provider configured with that data_type in the admin UI,
# plus a service account bound to it. Run the specific test for your provider's type:
#
#   ./scripts/test-ingest-parsed-text.sh     -k <api-key> [--cleanup]
#   ./scripts/test-ingest-chunked-text.sh    -k <api-key> [--cleanup]
#   ./scripts/test-ingest-full-documents.sh  -k <api-key> [--cleanup]
#
# Each script tests: ingest → idempotency → delete doc → (optional) delete collection.
#
# Setup:
#   1. Go to Admin → Settings → Integrations
#   2. Create a provider (e.g. "test-parsed") with data_type = "parsed_text"
#   3. Create a user, note its user ID
#   4. Set that user as the provider's service_account_id
#   5. Generate an API key for that user
#   6. Pass that API key to the test script
#
echo "This is a launcher — run one of the specific test scripts:"
echo ""
echo "  ./scripts/test-ingest-parsed-text.sh     -k <api-key> [--cleanup]"
echo "  ./scripts/test-ingest-chunked-text.sh    -k <api-key> [--cleanup]"
echo "  ./scripts/test-ingest-full-documents.sh  -k <api-key> [--cleanup]"
echo ""
echo "Each requires a provider configured with the matching data_type."
