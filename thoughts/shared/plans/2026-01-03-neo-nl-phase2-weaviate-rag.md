# NEO NL Phase 2: Weaviate RAG for File Uploads - Implementation Plan

## Overview

Enable document upload and RAG (Retrieval-Augmented Generation) capabilities for NEO NL users by adding Weaviate as the vector database and configuring OpenAI embeddings for document processing.

## Current State Analysis

Phase 1 provides the foundation:
- `docker-compose.neo.yaml` with Open WebUI service
- `.env.neo` with Hugging Face LLM configuration
- Dutch locale and authentication configured

Open WebUI's RAG system:
- Supports 10+ vector databases via factory pattern (`retrieval/vector/factory.py:10-75`)
- Weaviate client uses `weaviate.connect_to_local()` with HTTP + gRPC (`weaviate.py:66`)
- Embeddings handled by Open WebUI, not Weaviate (Weaviate uses `self_provided()` vectorizer)
- Knowledge feature routes: `routers/knowledge.py`, `routers/files.py`, `routers/retrieval.py`

### Key Discoveries

| Finding | Location | Impact |
|---------|----------|--------|
| Weaviate config vars | `config.py:2215-2218` | Set `WEAVIATE_HTTP_HOST`, `_PORT`, `_GRPC_PORT` |
| Vector DB selection | `config.py:2156` | Set `VECTOR_DB=weaviate` |
| OpenAI embeddings | `config.py:2718-2733` | Set `RAG_EMBEDDING_ENGINE=openai` |
| Separate embedding API | `config.py:2871-2876` | `RAG_OPENAI_API_KEY` separate from chat LLM |
| Collection name sanitization | `weaviate.py:71-93` | Auto-handles naming conventions |

## Desired End State

After Phase 2 completion:

1. **Weaviate service running** in Docker alongside Open WebUI
2. **Document uploads** working via Knowledge feature
3. **Documents embedded** using OpenAI `text-embedding-3-small`
4. **RAG queries** return relevant document chunks in chat
5. **Citations** appear in responses referencing uploaded documents

### Verification

```bash
# Weaviate container healthy
docker compose -f docker-compose.neo.yaml ps | grep weaviate

# Weaviate API accessible
curl -s http://localhost:8080/v1/.well-known/ready

# Check Weaviate schema (after document upload)
curl -s http://localhost:8080/v1/schema | jq '.classes[].class'
```

## What We're NOT Doing

- **MCP integration** - Phase 3
- **Custom Pipes** - Phase 3
- **External document collections** (IAEA, ANVS) - Phase 3 via MCP
- **Weaviate authentication** - Not needed for internal Docker network
- **Weaviate backups** - Operational concern, can add later
- **Hybrid search** - Can enable later via `ENABLE_RAG_HYBRID_SEARCH`

## Implementation Approach

1. Add Weaviate service to `docker-compose.neo.yaml`
2. Update `.env.neo.example` with Weaviate and embedding configuration
3. Update `.env.neo` with actual OpenAI API key for embeddings
4. Verify document upload → embedding → retrieval flow

---

## Phase 2.1: Add Weaviate Service to Docker Compose

### Overview

Extend `docker-compose.neo.yaml` with Weaviate service for vector storage.

### Changes Required

**File**: `docker-compose.neo.yaml` (update)

Replace the existing file with:

```yaml
# NEO NL Open WebUI Deployment with Weaviate RAG
# Usage: docker compose -f docker-compose.neo.yaml up -d
#
# Requires: .env.neo file with configuration (see .env.neo.example)

services:
  open-webui:
    image: ghcr.io/open-webui/open-webui:${WEBUI_DOCKER_TAG:-main}
    container_name: neo-nl-webui
    volumes:
      - neo-nl-data:/app/backend/data
    ports:
      - "${OPEN_WEBUI_PORT:-3000}:8080"
    env_file:
      - .env.neo
    depends_on:
      weaviate:
        condition: service_healthy
    extra_hosts:
      - host.docker.internal:host-gateway
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s

  weaviate:
    image: semitechnologies/weaviate:1.28.4
    container_name: neo-nl-weaviate
    environment:
      # Weaviate core settings
      QUERY_DEFAULTS_LIMIT: 25
      AUTHENTICATION_ANONYMOUS_ACCESS_ENABLED: "true"
      PERSISTENCE_DATA_PATH: /var/lib/weaviate
      # No vectorizer - Open WebUI provides embeddings externally
      DEFAULT_VECTORIZER_MODULE: none
      CLUSTER_HOSTNAME: node1
      # Logging
      LOG_LEVEL: info
    volumes:
      - weaviate-data:/var/lib/weaviate
    ports:
      # Only expose for debugging - can remove in production
      - "${WEAVIATE_HTTP_PORT:-8080}:8080"
      - "${WEAVIATE_GRPC_PORT:-50051}:50051"
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/v1/.well-known/ready"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 10s

volumes:
  neo-nl-data: {}
  weaviate-data: {}
```

### Key Configuration Choices

| Setting | Value | Reason |
|---------|-------|--------|
| Image version | `1.28.4` | Pinned stable version, not `latest` |
| `DEFAULT_VECTORIZER_MODULE` | `none` | Open WebUI provides embeddings |
| `AUTHENTICATION_ANONYMOUS_ACCESS_ENABLED` | `true` | Internal Docker network only |
| Port exposure | Configurable via env | Allow debugging but can be removed |
| `depends_on` with `service_healthy` | Added | Ensure Weaviate ready before Open WebUI starts |

### Success Criteria

#### Automated Verification
- [x] YAML syntax valid: `docker compose -f docker-compose.neo.yaml config`
- [x] Weaviate service defined in compose file
- [x] Healthcheck defined for Weaviate

---

## Phase 2.2: Update Environment Template

### Overview

Add Weaviate and RAG configuration to `.env.neo.example`.

### Changes Required

**File**: `.env.neo.example` (update)

Add the following sections after the existing LLM PROVIDERS section:

```bash
# ============================================================
# NEO NL Open WebUI Configuration
# ============================================================
# Copy this file to .env.neo and fill in the values
# Usage: docker compose -f docker-compose.neo.yaml --env-file .env.neo up -d

# ------------------------------------------------------------
# DEPLOYMENT
# ------------------------------------------------------------

# Docker image tag (main, latest, or specific version)
WEBUI_DOCKER_TAG=main

# Port to expose Open WebUI on (default: 3000)
OPEN_WEBUI_PORT=3000

# Secret key for session encryption
# Generate with: openssl rand -hex 32
# Leave empty for auto-generation (stored in container volume)
WEBUI_SECRET_KEY=

# ------------------------------------------------------------
# LOCALE & BRANDING
# ------------------------------------------------------------

# Default interface language (nl-NL for Dutch)
DEFAULT_LOCALE=nl-NL

# ------------------------------------------------------------
# AUTHENTICATION & ACCESS
# ------------------------------------------------------------

# Enable authentication (required)
WEBUI_AUTH=true

# Disable public signup - only admin can create users
ENABLE_SIGNUP=false

# Show login form (needed for username/password auth)
ENABLE_LOGIN_FORM=true

# Allow first user to register as admin (required when ENABLE_SIGNUP=false)
ENABLE_INITIAL_ADMIN_SIGNUP=true

# Default role for new users created by admin: pending, user, or admin
DEFAULT_USER_ROLE=user

# ------------------------------------------------------------
# LLM PROVIDERS
# ------------------------------------------------------------

# Disable Ollama (we use cloud APIs only)
ENABLE_OLLAMA_API=false

# Hugging Face OpenAI-compatible endpoint
# Get token from: https://huggingface.co/settings/tokens
# Create a fine-grained token with "Make calls to Inference Providers" permission
OPENAI_API_BASE_URL=https://router.huggingface.co/v1
OPENAI_API_KEY=hf_your-huggingface-token

# Model to use: openai/gpt-oss-120b (or other HF-hosted models)
# Models are selected in the Open WebUI interface

# Note: Additional providers can be added later via:
# OPENAI_API_KEYS=hf_key;openai_key (semicolon-separated)
# OPENAI_API_BASE_URLS=https://router.huggingface.co/v1;https://api.openai.com/v1

# ------------------------------------------------------------
# VECTOR DATABASE (Weaviate)
# ------------------------------------------------------------

# Select Weaviate as the vector database
VECTOR_DB=weaviate

# Weaviate connection settings (Docker service name)
WEAVIATE_HTTP_HOST=weaviate
WEAVIATE_HTTP_PORT=8080
WEAVIATE_GRPC_PORT=50051

# Optional: Weaviate API key (not needed for internal Docker network)
# WEAVIATE_API_KEY=

# External port exposure for debugging (can be removed in production)
# These are for docker-compose port mapping, not internal communication
# WEAVIATE_HTTP_PORT=8080
# WEAVIATE_GRPC_PORT=50051

# ------------------------------------------------------------
# RAG EMBEDDINGS (OpenAI)
# ------------------------------------------------------------

# Use OpenAI for embeddings (separate from chat LLM)
RAG_EMBEDDING_ENGINE=openai
RAG_EMBEDDING_MODEL=text-embedding-3-small

# OpenAI API key for embeddings
# Get from: https://platform.openai.com/api-keys
# Note: This is SEPARATE from the Hugging Face key used for chat
RAG_OPENAI_API_KEY=sk-your-openai-api-key

# Optional: Custom OpenAI endpoint for embeddings
# RAG_OPENAI_API_BASE_URL=https://api.openai.com/v1

# Embedding batch size (number of texts per API call)
RAG_EMBEDDING_BATCH_SIZE=100

# ------------------------------------------------------------
# RAG RETRIEVAL SETTINGS
# ------------------------------------------------------------

# Number of document chunks to retrieve per query
RAG_TOP_K=5

# Minimum relevance score (0.0 = no filtering)
RAG_RELEVANCE_THRESHOLD=0.0

# Chunk size for document splitting (characters)
CHUNK_SIZE=1000

# Overlap between chunks (characters)
CHUNK_OVERLAP=100

# Optional: Enable hybrid search (vector + BM25)
# ENABLE_RAG_HYBRID_SEARCH=true

# ------------------------------------------------------------
# FILE UPLOAD SETTINGS
# ------------------------------------------------------------

# Maximum file size in bytes (default: no limit)
# RAG_FILE_MAX_SIZE=52428800  # 50MB

# Maximum number of files per upload (default: no limit)
# RAG_FILE_MAX_COUNT=10

# Allowed file extensions (comma-separated, empty = all)
# RAG_ALLOWED_FILE_EXTENSIONS=.pdf,.txt,.md,.docx

# ------------------------------------------------------------
# TELEMETRY (disabled for privacy)
# ------------------------------------------------------------
SCARF_NO_ANALYTICS=true
DO_NOT_TRACK=true
ANONYMIZED_TELEMETRY=false
```

### Success Criteria

#### Automated Verification
- [x] File updated at `.env.neo.example`
- [x] Contains `VECTOR_DB=weaviate`
- [x] Contains `RAG_EMBEDDING_ENGINE=openai`
- [x] Contains `RAG_OPENAI_API_KEY` placeholder
- [x] No actual secrets in the file

---

## Phase 2.3: Update Actual Environment File

### Overview

Update `.env.neo` with actual OpenAI API key for embeddings.

### Changes Required

**File**: `.env.neo` (update)

Copy the new sections from `.env.neo.example` and fill in:

```bash
# Copy new sections from template
# Add after existing configuration:

# ------------------------------------------------------------
# VECTOR DATABASE (Weaviate)
# ------------------------------------------------------------
VECTOR_DB=weaviate
WEAVIATE_HTTP_HOST=weaviate
WEAVIATE_HTTP_PORT=8080
WEAVIATE_GRPC_PORT=50051

# ------------------------------------------------------------
# RAG EMBEDDINGS (OpenAI)
# ------------------------------------------------------------
RAG_EMBEDDING_ENGINE=openai
RAG_EMBEDDING_MODEL=text-embedding-3-small
RAG_OPENAI_API_KEY=sk-actual-openai-api-key-here

# ------------------------------------------------------------
# RAG RETRIEVAL SETTINGS
# ------------------------------------------------------------
RAG_TOP_K=5
RAG_RELEVANCE_THRESHOLD=0.0
CHUNK_SIZE=1000
CHUNK_OVERLAP=100
```

### Success Criteria

#### Automated Verification
- [x] `.env.neo` contains Weaviate configuration
- [x] `.env.neo` is gitignored: `git check-ignore .env.neo`

#### Manual Verification
- [x] OpenAI API key is valid: test with `curl` to embeddings endpoint
- [x] API key has access to `text-embedding-3-small` model

---

## Phase 2.4: Deploy and Verify Weaviate

### Overview

Start the updated deployment and verify Weaviate is healthy.

### Deployment Steps

```bash
# 1. Stop existing deployment (if running)
docker compose -f docker-compose.neo.yaml down

# 2. Pull new images
docker compose -f docker-compose.neo.yaml pull

# 3. Start updated deployment
docker compose -f docker-compose.neo.yaml up -d

# 4. Check both containers
docker compose -f docker-compose.neo.yaml ps

# 5. Watch logs for startup
docker compose -f docker-compose.neo.yaml logs -f
```

### Success Criteria

#### Automated Verification
- [x] Both containers running: `docker compose -f docker-compose.neo.yaml ps | grep -c "Up"` returns 2
- [x] Weaviate healthy: `curl -f http://localhost:8080/v1/.well-known/ready`
- [x] Open WebUI healthy: `curl -f http://localhost:3000/health`
- [x] Weaviate accessible from Open WebUI: check logs for connection success

```bash
# Verify Weaviate readiness
curl -s http://localhost:8080/v1/.well-known/ready | jq .

# Check Open WebUI RAG config via API (after login)
# The RAG settings should show Weaviate as the vector DB
```

#### Manual Verification
- [x] Open WebUI still accessible at `http://localhost:3000`
- [x] Can log in with existing admin account
- [x] No error messages in browser console about vector database

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation before proceeding to document upload testing.

---

## Phase 2.5: Test Document Upload and RAG

### Overview

Verify the complete document upload → embedding → retrieval flow.

### Test Steps

#### 5.1 Upload a Test Document

1. Log in to Open WebUI as admin
2. Navigate to **Workspace** → **Knowledge**
3. Click **"+"** or **"New Knowledge"** button
4. Create a knowledge base:
   - Name: "Test Documents"
   - Description: "Phase 2 RAG testing"
5. Click into the knowledge base
6. Upload a test document:
   - Use a simple `.txt` or `.pdf` with known content
   - Example: Create `test-doc.txt` with "The capital of the Netherlands is Amsterdam. The Netherlands is known for tulips, windmills, and cheese."
7. Wait for processing to complete (watch the status indicator)

#### 5.2 Verify Document in Weaviate

```bash
# List Weaviate collections (should show new collection)
curl -s http://localhost:8080/v1/schema | jq '.classes[].class'

# Get object count in collection
curl -s http://localhost:8080/v1/schema | jq '.classes[] | {class: .class, count: .vectorIndexConfig}'
```

#### 5.3 Test RAG Query

1. Start a new chat in Open WebUI
2. Before sending a message, click the **#** or file icon to attach the knowledge base
3. Select "Test Documents" knowledge base
4. Ask a question about the uploaded content:
   - "What is the capital of the Netherlands?"
   - "What is the Netherlands known for?"
5. Verify:
   - Response includes information from the uploaded document
   - Citation/source reference appears in the response

### Success Criteria

#### Automated Verification
- [ ] Weaviate schema has collections: `curl -s http://localhost:8080/v1/schema | jq '.classes | length > 0'`
- [ ] Document objects exist: check via Weaviate API

#### Manual Verification
- [ ] Knowledge feature visible in Workspace menu
- [ ] Can create new knowledge base
- [ ] Document upload completes successfully (no errors)
- [ ] Processing status shows "completed" or similar
- [ ] RAG query returns relevant content from uploaded document
- [ ] Citations/sources appear in chat responses
- [ ] Dutch interface maintained throughout

**Implementation Note**: After completing this phase and all verification passes, Phase 2 is complete. Proceed to Phase 3 for MCP integration.

---

## Testing Strategy

### Automated Tests

```bash
#!/bin/bash
# phase2-verify.sh

echo "=== Phase 2 Verification ==="

# 1. Check containers running
echo -n "Containers running: "
COUNT=$(docker compose -f docker-compose.neo.yaml ps --status running -q | wc -l)
if [ "$COUNT" -eq 2 ]; then echo "PASS ($COUNT/2)"; else echo "FAIL ($COUNT/2)"; fi

# 2. Weaviate health
echo -n "Weaviate healthy: "
if curl -sf http://localhost:8080/v1/.well-known/ready > /dev/null; then echo "PASS"; else echo "FAIL"; fi

# 3. Open WebUI health
echo -n "Open WebUI healthy: "
if curl -sf http://localhost:3000/health > /dev/null; then echo "PASS"; else echo "FAIL"; fi

# 4. Weaviate schema accessible
echo -n "Weaviate schema accessible: "
if curl -sf http://localhost:8080/v1/schema > /dev/null; then echo "PASS"; else echo "FAIL"; fi

# 5. Check env configuration
echo -n "VECTOR_DB set to weaviate: "
if grep -q "VECTOR_DB=weaviate" .env.neo; then echo "PASS"; else echo "FAIL"; fi

echo "=== End Verification ==="
```

### Manual Testing Checklist

1. **Document Upload Flow**
   - [ ] Create knowledge base
   - [ ] Upload PDF document
   - [ ] Upload TXT document
   - [ ] Upload DOCX document (if supported)
   - [ ] Processing completes for all formats

2. **RAG Query Flow**
   - [ ] Attach knowledge base to chat
   - [ ] Query returns relevant content
   - [ ] Multiple documents in same knowledge base work
   - [ ] Different knowledge bases can be selected

3. **Edge Cases**
   - [ ] Large document upload (test with 10+ page PDF)
   - [ ] Empty/minimal document
   - [ ] Document with special characters
   - [ ] Query with no relevant results (should gracefully handle)

---

## Troubleshooting

### Weaviate Connection Issues

```bash
# Check Weaviate logs
docker compose -f docker-compose.neo.yaml logs weaviate

# Verify network connectivity from Open WebUI
docker compose -f docker-compose.neo.yaml exec open-webui curl http://weaviate:8080/v1/.well-known/ready

# Check WEAVIATE_HTTP_HOST is set correctly
docker compose -f docker-compose.neo.yaml exec open-webui env | grep WEAVIATE
```

### Embedding Failures

```bash
# Check RAG configuration
docker compose -f docker-compose.neo.yaml exec open-webui env | grep RAG

# Test OpenAI embeddings directly
curl https://api.openai.com/v1/embeddings \
  -H "Authorization: Bearer $RAG_OPENAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"input": "test", "model": "text-embedding-3-small"}'

# Common issues:
# - Invalid API key
# - API key doesn't have embeddings access
# - Rate limiting
```

### Document Processing Stuck

```bash
# Check Open WebUI logs for processing errors
docker compose -f docker-compose.neo.yaml logs open-webui | grep -i "rag\|embed\|vector"

# Check file storage
docker compose -f docker-compose.neo.yaml exec open-webui ls -la /app/backend/data/uploads/

# Verify Weaviate is accepting data
curl -s http://localhost:8080/v1/schema | jq '.classes'
```

### Knowledge Feature Not Visible

```bash
# Check if feature is enabled (should be by default)
# If not, admin can enable in Settings → Features

# Verify user has permission
# Admin → Users → [User] → Permissions
```

---

## Performance Considerations

### Embedding Costs

- OpenAI `text-embedding-3-small`: ~$0.02 per 1M tokens
- Average PDF page: ~500 tokens
- 100-page document: ~$0.001 per document
- Embeddings are one-time cost per document

### Weaviate Resources

- Memory: ~512MB minimum, 1GB recommended
- Storage: Grows with document count
- Consider volume backup strategy for production

### Batch Processing

- `RAG_EMBEDDING_BATCH_SIZE=100` balances speed and memory
- Larger batches = faster processing, more memory
- Smaller batches = slower but more stable

---

## Files Modified

| File | Action | Commit |
|------|--------|--------|
| `docker-compose.neo.yaml` | Updated with Weaviate service | Yes |
| `.env.neo.example` | Updated with RAG configuration | Yes |
| `.env.neo` | Updated with actual values | No (gitignored) |

---

## Architecture After Phase 2

```
┌─────────────────────────────────────────────────────────────┐
│                        NEO NL Users                         │
└─────────────────────────────┬───────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                     Open WebUI (SvelteKit)                  │
│  ┌───────────────┐  ┌───────────────┐                       │
│  │   Chat UI     │  │  Knowledge UI │                       │
│  │   (Dutch)     │  │ (File Upload) │                       │
│  └───────┬───────┘  └───────┬───────┘                       │
└──────────┼──────────────────┼───────────────────────────────┘
           │                  │
           ▼                  ▼
┌─────────────────────────────────────────────────────────────┐
│               Open WebUI Backend (FastAPI)                  │
│  ┌───────────────┐  ┌───────────────┐  ┌─────────────────┐  │
│  │ LLM Provider  │  │   RAG System  │  │  File Storage   │  │
│  │ (HuggingFace) │  │   (Weaviate)  │  │                 │  │
│  └───────┬───────┘  └───────┬───────┘  └─────────────────┘  │
└──────────┼──────────────────┼───────────────────────────────┘
           │                  │
           ▼                  ▼
┌──────────────────┐  ┌──────────────────┐  ┌──────────────┐
│   Hugging Face   │  │     OpenAI       │  │   Weaviate   │
│   Router API     │  │   Embeddings     │  │   (Docker)   │
│   (Chat LLM)     │  │   API            │  │              │
└──────────────────┘  └──────────────────┘  └──────────────┘
```

**Data Flow for RAG**:
1. User uploads document via Knowledge UI
2. Document processed by loader (PDF, TXT, etc.)
3. Text chunked by `CHUNK_SIZE` and `CHUNK_OVERLAP`
4. Chunks sent to OpenAI Embeddings API
5. Vectors stored in Weaviate
6. User query embedded via same OpenAI API
7. Weaviate similarity search returns top-k chunks
8. Chunks injected into LLM prompt as context
9. LLM generates response with citations

---

## References

- Phase 1 plan: `thoughts/shared/plans/2026-01-03-neo-nl-phase1-deployment.md`
- Migration research: `thoughts/shared/research/neo-nl-migration-phases.md`
- Weaviate client: `backend/open_webui/retrieval/vector/dbs/weaviate.py`
- RAG config: `backend/open_webui/config.py:2622-2904`
- Knowledge routes: `backend/open_webui/routers/knowledge.py`
- Retrieval routes: `backend/open_webui/routers/retrieval.py`

---

*Created: 2026-01-03*
*Phase: 2 of 4 (NEO NL Migration)*
*Depends on: Phase 1 (Basic Deployment)*
