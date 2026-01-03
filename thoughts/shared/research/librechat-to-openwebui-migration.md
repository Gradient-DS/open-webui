# Research: LibreChat (soev.ai) to Open WebUI Migration

## Executive Summary

This document analyzes what code changes and docker compose configurations are needed to migrate from soev.ai (LibreChat fork) to Open WebUI. **The key finding is that Open WebUI has native support for most services soev.ai uses**, significantly reducing the need for custom code.

**Scope**: Create new docker compose files in the open-webui repo with minimal core changes.

---

## 1. Service Mapping Analysis

### soev.ai Services (from docker-compose.dev.yml / docker-compose.staging.yml)

| Service | Purpose | Open WebUI Equivalent |
|---------|---------|----------------------|
| MongoDB | Primary database | SQLite (default) or PostgreSQL |
| MeiliSearch | Full-text search | Not needed (different architecture) |
| pgvector (vectordb) | Vector storage for RAG | Native: `VECTOR_DB=pgvector` |
| rag_api | LibreChat RAG API | Built-in RAG system |
| SearXNG | Web search | Native: `RAG_WEB_SEARCH_ENGINE=searxng` |
| Firecrawl | Web scraping | Built-in Playwright loader OR Tool function |
| Prometheus/Grafana | Monitoring | Native: `docker-compose.otel.yaml` pattern |
| NGINX | Reverse proxy | Optional (direct or via Caddy/Traefik) |

### What Gets Eliminated

- **MongoDB**: Open WebUI uses SQLite by default, Postgres optional
- **MeiliSearch**: Open WebUI has different search architecture
- **rag_api**: Open WebUI has built-in RAG with multiple vector DB backends

---

## 2. Open WebUI Native Capabilities (No Custom Code Needed)

### 2.1 Vector Database Support

Open WebUI has **native Weaviate support** via `backend/open_webui/retrieval/vector/dbs/weaviate.py`:

```bash
# Environment variables for Weaviate
VECTOR_DB=weaviate
WEAVIATE_HTTP_HOST=weaviate
WEAVIATE_HTTP_PORT=8080
WEAVIATE_GRPC_PORT=50051
WEAVIATE_API_KEY=  # Optional
```

Other supported vector DBs (via `retrieval/vector/factory.py`):
- `chroma` (default, local)
- `weaviate` (what we want)
- `qdrant` (with multitenancy option)
- `pgvector` (PostgreSQL extension)
- `milvus` (with multitenancy option)
- `elasticsearch`
- `opensearch`
- `pinecone`

### 2.2 Web Search Support

Native SearXNG support:

```bash
ENABLE_RAG_WEB_SEARCH=true
RAG_WEB_SEARCH_ENGINE=searxng
SEARXNG_QUERY_URL=http://searxng:8080/search?q=<query>
```

13+ search providers supported out of the box.

### 2.3 Embedding & Reranking

```bash
# Embedding options
RAG_EMBEDDING_ENGINE=openai  # or ollama, sentence-transformers
RAG_EMBEDDING_MODEL=text-embedding-3-small
OPENAI_API_KEY=sk-...

# Reranking (built-in options)
RAG_RERANKING_ENGINE=  # cohere, jina, sentence-transformers
RAG_RERANKING_MODEL=
# OR external reranker
RAG_EXTERNAL_RERANKER_URL=http://reranker:8000
RAG_EXTERNAL_RERANKER_API_KEY=
```

### 2.4 Web Scraping (Playwright)

Open WebUI has a `docker-compose.playwright.yaml` for web content loading:

```yaml
# Already exists in repo - provides browser-based web loading
```

---

## 3. Docker Compose Files to Create

Based on the analysis, we need these new compose files:

### 3.1 `docker-compose.neo.yaml` - Core NEO NL Setup

```yaml
# Main production-like setup with Weaviate
services:
  open-webui:
    image: ghcr.io/open-webui/open-webui:main
    environment:
      # Core
      - WEBUI_SECRET_KEY=${WEBUI_SECRET_KEY}
      - WEBUI_AUTH=true
      - ENABLE_SIGNUP=false
      - DEFAULT_LOCALE=nl

      # Branding
      - WEBUI_NAME=NEO NL AI Assistant
      - WEBUI_BANNER_CONTENT=Welkom bij de NEO NL AI Assistent

      # Vector DB (native Weaviate support!)
      - VECTOR_DB=weaviate
      - WEAVIATE_HTTP_HOST=weaviate
      - WEAVIATE_HTTP_PORT=8080
      - WEAVIATE_GRPC_PORT=50051

      # RAG
      - RAG_EMBEDDING_ENGINE=openai
      - RAG_EMBEDDING_MODEL=${RAG_EMBEDDING_MODEL:-text-embedding-3-small}

      # Web Search
      - ENABLE_RAG_WEB_SEARCH=true
      - RAG_WEB_SEARCH_ENGINE=searxng
      - SEARXNG_QUERY_URL=http://searxng:8080/search?q=<query>

      # API Keys
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}

    volumes:
      - open-webui-data:/app/backend/data
    ports:
      - "3000:8080"
    depends_on:
      - weaviate
      - searxng

  weaviate:
    image: semitechnologies/weaviate:latest
    environment:
      - QUERY_DEFAULTS_LIMIT=25
      - AUTHENTICATION_ANONYMOUS_ACCESS_ENABLED=true
      - PERSISTENCE_DATA_PATH=/var/lib/weaviate
      - DEFAULT_VECTORIZER_MODULE=none
      - CLUSTER_HOSTNAME=node1
    volumes:
      - weaviate-data:/var/lib/weaviate
    ports:
      - "8080:8080"
      - "50051:50051"

  searxng:
    image: searxng/searxng:latest
    volumes:
      - ./searxng:/etc/searxng
    environment:
      - SEARXNG_BASE_URL=http://searxng:8080

volumes:
  open-webui-data:
  weaviate-data:
```

### 3.2 `docker-compose.neo-dev.yaml` - Development Override

```yaml
# Development overrides - extends neo.yaml
services:
  open-webui:
    build:
      context: .
      dockerfile: Dockerfile
    volumes:
      - ./backend:/app/backend  # Hot reload
      - open-webui-data:/app/backend/data
    environment:
      - GLOBAL_LOG_LEVEL=DEBUG
```

### 3.3 `docker-compose.neo-monitoring.yaml` - With Observability

```yaml
# Extends otel.yaml pattern for monitoring
services:
  grafana:
    image: grafana/otel-lgtm:latest
    ports:
      - "3001:3000"  # Grafana UI (offset from main app)
      - "4317:4317"  # OTLP/gRPC
      - "4318:4318"  # OTLP/HTTP

  open-webui:
    environment:
      - ENABLE_OTEL=true
      - ENABLE_OTEL_METRICS=true
      - OTEL_EXPORTER_OTLP_ENDPOINT=http://grafana:4317
      - OTEL_SERVICE_NAME=neo-nl-ai
```

### 3.4 `docker-compose.neo-postgres.yaml` - PostgreSQL Backend

```yaml
# For production with PostgreSQL instead of SQLite
services:
  postgres:
    image: postgres:16
    environment:
      - POSTGRES_USER=openwebui
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
      - POSTGRES_DB=openwebui
    volumes:
      - postgres-data:/var/lib/postgresql/data

  open-webui:
    environment:
      - DATABASE_URL=postgresql://openwebui:${POSTGRES_PASSWORD}@postgres:5432/openwebui
    depends_on:
      - postgres

volumes:
  postgres-data:
```

---

## 4. Migration Matrix - What Requires Code

### 4.1 Zero Code Changes (Use Native Features)

| Feature | Implementation |
|---------|---------------|
| Weaviate RAG | `VECTOR_DB=weaviate` + env vars |
| SearXNG search | `RAG_WEB_SEARCH_ENGINE=searxng` |
| User auth & RBAC | Built-in Admin Panel |
| Dutch locale | `DEFAULT_LOCALE=nl` |
| OpenAI/Anthropic | `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` |
| Branding | `WEBUI_NAME`, `WEBUI_BANNER_CONTENT` |
| Monitoring | `docker-compose.otel.yaml` pattern |

### 4.2 Optional Custom Functions (Only if Needed)

| Feature | When Needed | Implementation |
|---------|-------------|----------------|
| Firecrawl Tool | If keeping Firecrawl service | Python Tool function |
| Custom Reranker | If external reranker needed | Use `RAG_EXTERNAL_RERANKER_URL` |
| Custom RAG logic | If special document handling needed | Python Pipe function |

---

## 5. Key Differences from Previous Migration Guide

The earlier agent's guide proposed writing custom **Pipe functions** for Weaviate RAG. This is **unnecessary** because:

1. **Open WebUI has native Weaviate support** (`backend/open_webui/retrieval/vector/dbs/weaviate.py`)
2. Just set `VECTOR_DB=weaviate` and configure connection env vars
3. The built-in RAG system handles embedding, search, and context injection

### Corrected Approach

| Previous Guide | Actual Implementation |
|----------------|----------------------|
| Write `weaviate_rag_pipe.py` | Use `VECTOR_DB=weaviate` |
| Write `reranker_filter.py` | Use `RAG_EXTERNAL_RERANKER_URL` |
| Custom RAG context formatting | Built-in RAG with configurable `RAG_TEMPLATE` |

---

## 6. Data Migration Considerations

### 6.1 User Migration

- soev.ai users are in MongoDB
- Open WebUI uses SQLite/Postgres
- Options:
  1. Manual recreation via Admin Panel
  2. Script via Open WebUI API (`/api/v1/auths/signup`)
  3. OAuth integration (users recreated on first login)

### 6.2 Document/Vector Migration

If keeping existing Weaviate data:
- Collections should work if schema matches Open WebUI expectations
- May need to verify collection naming (Open WebUI sanitizes names)

If fresh start:
- Re-ingest documents via Open WebUI Knowledge feature
- Or use genai-utils pipeline to populate Weaviate directly

### 6.3 Chat History

- No direct migration path (different schemas)
- Accept history loss or export/archive from LibreChat

---

## 7. Environment Variables Reference

### Required

```bash
# Security
WEBUI_SECRET_KEY=            # Generate: openssl rand -hex 32

# API Keys
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...  # Optional
```

### Weaviate Connection

```bash
VECTOR_DB=weaviate
WEAVIATE_HTTP_HOST=weaviate  # or IP/hostname
WEAVIATE_HTTP_PORT=8080
WEAVIATE_GRPC_PORT=50051
WEAVIATE_API_KEY=            # If auth enabled
```

### RAG Configuration

```bash
RAG_EMBEDDING_ENGINE=openai
RAG_EMBEDDING_MODEL=text-embedding-3-small
RAG_TOP_K=5
RAG_RELEVANCE_THRESHOLD=0.0
ENABLE_RAG_HYBRID_SEARCH=true
RAG_HYBRID_BM25_WEIGHT=0.5
```

### Branding & Locale

```bash
WEBUI_NAME=NEO NL AI Assistant
WEBUI_BANNER_CONTENT=Welkom bij de NEO NL AI Assistent
DEFAULT_LOCALE=nl
ENABLE_SIGNUP=false
```

### Feature Toggles

```bash
ENABLE_IMAGE_GENERATION=false
ENABLE_CODE_EXECUTION=false
ENABLE_COMMUNITY_SHARING=false
ENABLE_MESSAGE_RATING=true
```

---

## 8. File Structure for Migration

```
open-webui/
├── docker-compose.yaml              # Existing (base)
├── docker-compose.neo.yaml          # NEW: NEO NL production
├── docker-compose.neo-dev.yaml      # NEW: Development overrides
├── docker-compose.neo-monitoring.yaml  # NEW: With OTEL
├── docker-compose.neo-postgres.yaml # NEW: PostgreSQL backend
├── .env.neo.example                 # NEW: NEO-specific env template
├── searxng/                         # NEW: SearXNG config
│   └── settings.yml
└── thoughts/shared/research/
    └── librechat-to-openwebui-migration.md  # This file
```

---

## 9. Implementation Checklist

### Phase 1: Infrastructure (Docker Compose)
- [ ] Create `docker-compose.neo.yaml`
- [ ] Create `.env.neo.example`
- [ ] Configure SearXNG settings
- [ ] Test basic deployment

### Phase 2: Configuration
- [ ] Configure branding env vars
- [ ] Set up API keys
- [ ] Configure Weaviate connection
- [ ] Test RAG functionality

### Phase 3: Users & Access
- [ ] Create admin user
- [ ] Set up user groups
- [ ] Configure permissions
- [ ] Test RBAC

### Phase 4: Optional Enhancements
- [ ] Add monitoring (OTEL compose)
- [ ] Add PostgreSQL for production
- [ ] Custom functions if needed

---

## 10. Key Codebase Locations

For reference when troubleshooting or extending:

| Feature | Location in open-webui |
|---------|------------------------|
| Vector DB factory | `backend/open_webui/retrieval/vector/factory.py` |
| Weaviate client | `backend/open_webui/retrieval/vector/dbs/weaviate.py` |
| Web search | `backend/open_webui/retrieval/web/` |
| Config/env vars | `backend/open_webui/config.py` |
| Functions system | `backend/open_webui/routers/functions.py` |
| Tools system | `backend/open_webui/routers/tools.py` |
| RAG retrieval | `backend/open_webui/routers/retrieval.py` |
| User/RBAC | `backend/open_webui/models/users.py`, `groups.py` |

---

## 11. Conclusion

**The migration is simpler than initially estimated** because Open WebUI has native support for:
- Weaviate vector database
- SearXNG web search
- External rerankers
- OpenTelemetry monitoring

**Minimal code changes needed** - primarily just:
1. New docker-compose files
2. Environment configuration
3. Optional: Custom Tool functions for Firecrawl if that workflow is kept

**Estimated effort**: 2-3 days for basic migration, +1-2 days for monitoring and production hardening.

---

*Research completed: 2026-01-03*
*Based on: open-webui codebase analysis and soev.ai docker-compose files*
