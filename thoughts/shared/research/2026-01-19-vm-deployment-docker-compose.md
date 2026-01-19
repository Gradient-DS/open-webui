---
date: 2026-01-19T12:00:00+01:00
researcher: Claude
git_commit: e662a9946c75500b14dc7ed0e3486f064556caa4
branch: main
repository: open-webui
topic: "Two-VM Docker Compose Deployment for Open WebUI"
tags: [research, deployment, docker-compose, infrastructure, vm, web-search]
status: complete
last_updated: 2026-01-19
last_updated_by: Claude
---

# Research: Two-VM Docker Compose Deployment for Open WebUI

**Date**: 2026-01-19T12:00:00+01:00
**Researcher**: Claude
**Git Commit**: e662a9946c75500b14dc7ed0e3486f064556caa4
**Branch**: main
**Repository**: open-webui

## Research Question

Create two docker-compose files to deploy Open WebUI on two VMs where monitoring is not required:
1. **Application VM**: Open WebUI and its databases
2. **Web Search VM**: Gateway and web search related services (SearXNG, Playwright, Reranker)

Similar to the LibreChat two-VM deployment pattern previously used.

## Summary

Created two docker-compose files for a split VM deployment:

| File | VM | Services | Ports |
|------|-----|----------|-------|
| `docker-compose.app-vm.yaml` | Application | Open WebUI, PostgreSQL, Nginx, Certbot | 80, 443 |
| `docker-compose.websearch-vm.yaml` | Web Search | SearXNG, Playwright, Reranker | 8080, 3000, 8086 |

The VMs communicate over the network with the Application VM making HTTP/WebSocket requests to the Web Search VM's exposed services.

## Detailed Findings

### Open WebUI Service Dependencies

**Required:**
- Database: SQLite (default) or PostgreSQL (recommended for production)
- ChromaDB: Embedded by default for RAG vector storage

**Optional:**
- Redis: For multi-instance deployments with shared sessions
- External LLM: Ollama or OpenAI-compatible API
- Vector Database: Supports 9+ options (Chroma, Milvus, Qdrant, Weaviate, pgvector, etc.)

### Web Search Architecture in Open WebUI

Open WebUI supports 22+ search engines and 5 web loader engines:

**Search Engines (notable):**
- SearXNG (self-hosted, no API key needed)
- DuckDuckGo (no API key needed)
- Brave, Google PSE, Bing (API key required)
- Firecrawl (API key required)

**Web Loaders:**
- `safe_web`: Default HTTP-based loader
- `playwright`: JavaScript rendering for dynamic content
- `firecrawl`: Batch scraping with markdown output
- `tavily`: Extraction with search
- `external`: Custom endpoint

### Files Created

1. **`docker-compose.app-vm.yaml`** - Application VM stack
   - Open WebUI (Gradient-DS image)
   - PostgreSQL 16 database
   - Nginx reverse proxy with SSL
   - Certbot for Let's Encrypt certificates

2. **`docker-compose.websearch-vm.yaml`** - Web Search VM stack
   - SearXNG (meta search engine)
   - Playwright (JavaScript-capable web scraper)
   - Infinity Reranker (semantic reranking)

3. **`env.app-vm.example`** - Environment configuration template

4. **`nginx/nginx.conf.example`** - Nginx configuration for SSL termination

### Network Configuration

The two VMs communicate as follows:

```
┌─────────────────────────────────────┐     ┌─────────────────────────────────────┐
│         Application VM              │     │         Web Search VM               │
│                                     │     │                                     │
│  ┌─────────────┐  ┌──────────────┐ │     │  ┌─────────────┐                    │
│  │   Nginx     │  │  PostgreSQL  │ │     │  │  SearXNG    │ :8080              │
│  │   :80/443   │  │              │ │     │  └─────────────┘                    │
│  └──────┬──────┘  └──────────────┘ │     │                                     │
│         │                          │     │  ┌─────────────┐                    │
│  ┌──────▼──────┐                   │     │  │ Playwright  │ :3000 (WebSocket)  │
│  │ Open WebUI  │ ─────────────────────────▶│  └─────────────┘                    │
│  │   :8080     │    HTTP/WS        │     │                                     │
│  └─────────────┘                   │     │  ┌─────────────┐                    │
│                                     │     │  │  Reranker   │ :8086              │
└─────────────────────────────────────┘     │  └─────────────┘                    │
                                            └─────────────────────────────────────┘
```

**Application VM → Web Search VM connections:**
- `SEARXNG_QUERY_URL=http://<websearch-vm-ip>:8080/search`
- `PLAYWRIGHT_WS_URL=ws://<websearch-vm-ip>:3000`
- `RAG_EXTERNAL_RERANKER_URL=http://<websearch-vm-ip>:8086/rerank`

### Environment Variables for Web Search

Key environment variables on the Application VM:

```bash
# Enable web search
ENABLE_WEB_SEARCH=true
WEB_SEARCH_ENGINE=searxng

# SearXNG configuration
SEARXNG_QUERY_URL=http://<websearch-vm-ip>:8080/search

# Playwright for JavaScript rendering
WEB_LOADER_ENGINE=playwright
PLAYWRIGHT_WS_URL=ws://<websearch-vm-ip>:3000
PLAYWRIGHT_TIMEOUT=30000

# Optional: Reranking
RAG_RERANKING_ENGINE=external
RAG_EXTERNAL_RERANKER_URL=http://<websearch-vm-ip>:8086/rerank
```

### Comparison with LibreChat Deployment

| Component | LibreChat | Open WebUI |
|-----------|-----------|------------|
| Search Engine | SearXNG | SearXNG |
| Web Scraper | Firecrawl + Playwright | Playwright (built-in support) |
| Reranker | Custom Jina proxy | Infinity (cross-encoder) |
| Database | MongoDB | PostgreSQL |
| Cache | Redis (for Firecrawl) | Not required |

Open WebUI is simpler because:
- Playwright is natively supported without needing Firecrawl
- No Redis required for the web search stack
- Direct integration with SearXNG via built-in adapter

## Code References

- `docker-compose.websearch.yaml` - Existing web search stack reference
- `docker-compose.staging.yaml` - PostgreSQL deployment reference
- `backend/open_webui/config.py:3046-3388` - Web search configuration
- `backend/open_webui/retrieval/web/utils.py:653-725` - Web loader factory
- `searxng/settings.yml` - SearXNG configuration

## Architecture Insights

1. **Separation of concerns**: Web search services are stateless and can be scaled independently
2. **Resource isolation**: Playwright and Firecrawl are memory-intensive; isolating them prevents OOM issues affecting the main application
3. **Security**: Web search VM can be placed in a separate network zone with restricted egress
4. **Flexibility**: Can swap SearXNG for other search providers without touching app VM

## Deployment Steps

### Web Search VM (deploy first)

```bash
# 1. SSH to web search VM
# 2. Clone repo and navigate to directory
git clone https://github.com/Gradient-DS/open-webui.git
cd open-webui

# 3. Start services
docker compose -f docker-compose.websearch-vm.yaml up -d

# 4. Verify services
curl http://localhost:8080/search?q=test&format=json
```

### Application VM

```bash
# 1. SSH to application VM
# 2. Clone repo and navigate to directory
git clone https://github.com/Gradient-DS/open-webui.git
cd open-webui

# 3. Configure environment
cp env.app-vm.example .env.app-vm
# Edit .env.app-vm - set WEB_SEARCH_VM_IP and other values

# 4. Configure nginx
cp nginx/nginx.conf.example nginx/nginx.conf
# Edit nginx/nginx.conf - replace YOUR_FQDN with actual domain

# 5. Create data directories
sudo mkdir -p /srv/openwebui/{postgres,uploads,certbot/{conf,www,log}}
sudo chown -R $USER:$USER /srv/openwebui

# 6. Obtain SSL certificate (first time)
docker run -it --rm -v /srv/openwebui/certbot/conf:/etc/letsencrypt \
  -v /srv/openwebui/certbot/www:/var/www/certbot \
  certbot/certbot certonly --webroot -w /var/www/certbot \
  -d YOUR_FQDN --email YOUR_EMAIL --agree-tos

# 7. Start services
docker compose -f docker-compose.app-vm.yaml up -d
```

## Open Questions

1. **Firecrawl support**: If Firecrawl is preferred over Playwright, a third compose file could add it with its dependencies (Redis, PostgreSQL)
2. **GPU acceleration**: If the Web Search VM has a GPU, the reranker image could use `michaelf34/infinity:latest-gpu`
3. **Scaling**: For high traffic, consider multiple Playwright instances behind a load balancer
4. **Firewall rules**: Document specific IP/port allowlist between VMs
