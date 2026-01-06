# Voorbeeld.soev.ai Deployment Implementation Plan

## Overview

Create deployment configuration for Open WebUI to be accessible at `https://voorbeeld.soev.ai` using Docker Compose with Caddy as reverse proxy. Also create a staging configuration for localhost development.

## Current State Analysis

- **Docker Image**: Built via GitHub Actions to `ghcr.io/gradient-ds/open-webui` with tags: `main`, `latest`, `cuda`, `ollama`, `slim`
- **Application Port**: 8080 (internal)
- **Health Check**: `/health` endpoint
- **Existing Configs**: `docker-compose.yaml` (basic), `docker-compose.neo.yaml` (with Weaviate)

### Key Discoveries:
- Open WebUI runs on port 8080 internally (Dockerfile:173)
- Health check uses curl to `/health` endpoint (Dockerfile:175)
- PostgreSQL connection via `DATABASE_URL` or individual `DATABASE_*` vars (env.py:257-278)
- Session encryption requires `WEBUI_SECRET_KEY` (env.py:450-455)
- Data persistence at `/app/backend/data` (env.py:210)

## Desired End State

1. **Production**: `docker-compose.demo.yaml` with:
   - Open WebUI container from GHCR
   - PostgreSQL container for database
   - Caddy container for automatic HTTPS
   - Persistent volumes for data and DB
   - Accessible at `https://voorbeeld.soev.ai`

2. **Staging**: `docker-compose.staging.yaml` with:
   - Open WebUI container
   - PostgreSQL container
   - Accessible at `http://localhost:3000`
   - No Caddy (direct port exposure)

3. **Environment Files**:
   - `.env.demo.example` - Production template
   - `.env.staging.example` - Staging template

## What We're NOT Doing

- No Ollama integration (cloud APIs only)
- No Weaviate/RAG setup (can be added later)
- No Redis/clustering (single instance)
- No CUDA/GPU support (CPU only image)
- No custom branding configuration

## Implementation Approach

Create two separate docker-compose files optimized for their use cases:
- Production uses Caddy for automatic HTTPS certificate management
- Staging uses simple port forwarding for local development

---

## Phase 1: Create Staging Configuration

### Overview
Create a minimal staging configuration for localhost development and testing.

### Changes Required:

#### 1. Docker Compose Staging File
**File**: `docker-compose.staging.yaml`

```yaml
# Open WebUI Staging Configuration
# Usage: docker compose -f docker-compose.staging.yaml up -d
# Access at: http://localhost:3000

services:
  open-webui:
    image: ghcr.io/gradient-ds/open-webui:${WEBUI_DOCKER_TAG:-main}
    container_name: staging-webui
    volumes:
      - staging-data:/app/backend/data
    ports:
      - "${OPEN_WEBUI_PORT:-3000}:8080"
    env_file:
      - .env.staging
    depends_on:
      postgres:
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

  postgres:
    image: postgres:16-alpine
    container_name: staging-postgres
    environment:
      POSTGRES_USER: ${DATABASE_USER:-openwebui}
      POSTGRES_PASSWORD: ${DATABASE_PASSWORD:-staging_password}
      POSTGRES_DB: ${DATABASE_NAME:-openwebui}
    volumes:
      - staging-postgres:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${DATABASE_USER:-openwebui} -d ${DATABASE_NAME:-openwebui}"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 10s
    restart: unless-stopped

volumes:
  staging-data: {}
  staging-postgres: {}
```

#### 2. Staging Environment Template
**File**: `.env.staging.example`

```bash
# ============================================================
# Open WebUI Staging Configuration
# ============================================================
# Copy to .env.staging and fill in values
# Usage: docker compose -f docker-compose.staging.yaml up -d

# ------------------------------------------------------------
# DEPLOYMENT
# ------------------------------------------------------------
WEBUI_DOCKER_TAG=main
OPEN_WEBUI_PORT=3000

# Secret key for session encryption (generate: openssl rand -hex 32)
WEBUI_SECRET_KEY=your-staging-secret-key-here

# ------------------------------------------------------------
# DATABASE (PostgreSQL)
# ------------------------------------------------------------
DATABASE_TYPE=postgresql
DATABASE_USER=openwebui
DATABASE_PASSWORD=staging_password
DATABASE_HOST=postgres
DATABASE_PORT=5432
DATABASE_NAME=openwebui

# ------------------------------------------------------------
# AUTHENTICATION
# ------------------------------------------------------------
WEBUI_AUTH=true
ENABLE_SIGNUP=true
ENABLE_LOGIN_FORM=true
ENABLE_INITIAL_ADMIN_SIGNUP=true
DEFAULT_USER_ROLE=user

# ------------------------------------------------------------
# LLM PROVIDERS
# ------------------------------------------------------------
ENABLE_OLLAMA_API=false

# OpenAI-compatible API
OPENAI_API_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=sk-your-openai-key

# ------------------------------------------------------------
# CORS & SECURITY (relaxed for staging)
# ------------------------------------------------------------
CORS_ALLOW_ORIGIN=*

# ------------------------------------------------------------
# TELEMETRY (disabled)
# ------------------------------------------------------------
SCARF_NO_ANALYTICS=true
DO_NOT_TRACK=true
ANONYMIZED_TELEMETRY=false
```

### Success Criteria:

#### Automated Verification:
- [ ] `docker compose -f docker-compose.staging.yaml config` validates successfully
- [ ] `docker compose -f docker-compose.staging.yaml up -d` starts all services
- [ ] `docker compose -f docker-compose.staging.yaml ps` shows healthy status
- [ ] `curl http://localhost:3000/health` returns healthy response

#### Manual Verification:
- [ ] Open `http://localhost:3000` in browser
- [ ] Create admin account via initial signup
- [ ] Verify login/logout works
- [ ] Test chat functionality with configured API

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to the next phase.

---

## Phase 2: Create Production Configuration with Caddy

### Overview
Create production configuration with Caddy for automatic HTTPS at voorbeeld.soev.ai.

### Changes Required:

#### 1. Docker Compose Production File
**File**: `docker-compose.demo.yaml`

```yaml
# Open WebUI Production Demo Configuration for voorbeeld.soev.ai
# Usage: docker compose -f docker-compose.demo.yaml up -d
# Access at: https://voorbeeld.soev.ai
#
# Prerequisites:
# 1. DNS A record for voorbeeld.soev.ai pointing to server IP
# 2. Ports 80 and 443 open on firewall
# 3. Copy .env.demo.example to .env.demo and configure

services:
  caddy:
    image: caddy:2-alpine
    container_name: demo-caddy
    ports:
      - "80:80"
      - "443:443"
      - "443:443/udp"
    volumes:
      - ./Caddyfile.demo:/etc/caddy/Caddyfile:ro
      - caddy-data:/data
      - caddy-config:/config
    depends_on:
      open-webui:
        condition: service_healthy
    restart: unless-stopped

  open-webui:
    image: ghcr.io/gradient-ds/open-webui:${WEBUI_DOCKER_TAG:-main}
    container_name: demo-webui
    volumes:
      - demo-data:/app/backend/data
    expose:
      - "8080"
    env_file:
      - .env.demo
    depends_on:
      postgres:
        condition: service_healthy
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s

  postgres:
    image: postgres:16-alpine
    container_name: demo-postgres
    environment:
      POSTGRES_USER: ${DATABASE_USER:-openwebui}
      POSTGRES_PASSWORD: ${DATABASE_PASSWORD}
      POSTGRES_DB: ${DATABASE_NAME:-openwebui}
    volumes:
      - demo-postgres:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${DATABASE_USER:-openwebui} -d ${DATABASE_NAME:-openwebui}"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 10s
    restart: unless-stopped

volumes:
  caddy-data: {}
  caddy-config: {}
  demo-data: {}
  demo-postgres: {}
```

#### 2. Caddyfile for HTTPS
**File**: `Caddyfile.demo`

```caddyfile
# Caddyfile for voorbeeld.soev.ai
# Automatic HTTPS via Let's Encrypt

voorbeeld.soev.ai {
    # Reverse proxy to Open WebUI
    reverse_proxy open-webui:8080 {
        # WebSocket support
        header_up Host {host}
        header_up X-Real-IP {remote_host}
        header_up X-Forwarded-For {remote_host}
        header_up X-Forwarded-Proto {scheme}
    }

    # Enable compression
    encode gzip zstd

    # Logging
    log {
        output file /data/access.log {
            roll_size 10mb
            roll_keep 5
        }
    }
}
```

#### 3. Production Environment Template
**File**: `.env.demo.example`

```bash
# ============================================================
# Open WebUI Demo Configuration - voorbeeld.soev.ai
# ============================================================
# Copy to .env.demo and fill in values
# Usage: docker compose -f docker-compose.demo.yaml up -d

# ------------------------------------------------------------
# DEPLOYMENT
# ------------------------------------------------------------
WEBUI_DOCKER_TAG=main
# WEBUI_DOCKER_TAG=v0.5.0  # Pin to specific version for production

# Secret key for session encryption (REQUIRED - generate: openssl rand -hex 32)
WEBUI_SECRET_KEY=

# ------------------------------------------------------------
# DATABASE (PostgreSQL)
# ------------------------------------------------------------
DATABASE_TYPE=postgresql
DATABASE_USER=openwebui
DATABASE_PASSWORD=  # REQUIRED - use strong password
DATABASE_HOST=postgres
DATABASE_PORT=5432
DATABASE_NAME=openwebui

# ------------------------------------------------------------
# AUTHENTICATION
# ------------------------------------------------------------
WEBUI_AUTH=true
ENABLE_SIGNUP=false
ENABLE_LOGIN_FORM=true
ENABLE_INITIAL_ADMIN_SIGNUP=true
DEFAULT_USER_ROLE=user

# ------------------------------------------------------------
# LLM PROVIDERS
# ------------------------------------------------------------
ENABLE_OLLAMA_API=false

# OpenAI-compatible API (configure your provider)
OPENAI_API_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=  # REQUIRED

# Alternative: HuggingFace Inference API
# OPENAI_API_BASE_URL=https://router.huggingface.co/v1
# OPENAI_API_KEY=hf_your-token

# ------------------------------------------------------------
# SECURITY (production hardened)
# ------------------------------------------------------------
# Trust Caddy's forwarded headers
FORWARDED_ALLOW_IPS=172.16.0.0/12

# Secure cookies (required for HTTPS)
WEBUI_SESSION_COOKIE_SECURE=true
WEBUI_AUTH_COOKIE_SECURE=true

# ------------------------------------------------------------
# TELEMETRY (disabled)
# ------------------------------------------------------------
SCARF_NO_ANALYTICS=true
DO_NOT_TRACK=true
ANONYMIZED_TELEMETRY=false
```

### Success Criteria:

#### Automated Verification:
- [ ] `docker compose -f docker-compose.demo.yaml config` validates successfully
- [ ] Caddyfile syntax valid: `docker run --rm -v $(pwd)/Caddyfile.demo:/etc/caddy/Caddyfile:ro caddy:2-alpine caddy validate --config /etc/caddy/Caddyfile`
- [ ] `docker compose -f docker-compose.demo.yaml up -d` starts all services
- [ ] `docker compose -f docker-compose.demo.yaml ps` shows all containers healthy

#### Manual Verification:
- [ ] DNS record configured for voorbeeld.soev.ai
- [ ] `https://voorbeeld.soev.ai` loads with valid SSL certificate
- [ ] HTTP to HTTPS redirect works
- [ ] Create admin account and verify login
- [ ] Test chat functionality

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to the next phase.

---

## Phase 3: Deployment Documentation

### Overview
Create deployment guide for VM setup.

### Changes Required:

#### 1. Deployment README
**File**: `docs/deployment-demo.md`

See the actual file for full documentation.

### Success Criteria:

#### Automated Verification:
- [ ] Documentation file exists at `docs/deployment-demo.md`
- [ ] All code blocks are valid bash/yaml syntax

#### Manual Verification:
- [ ] Documentation is clear and complete
- [ ] All commands in documentation work as expected

---

## Testing Strategy

### Local Staging Testing:
1. Start staging: `docker compose -f docker-compose.staging.yaml up -d`
2. Access http://localhost:3000
3. Create admin account
4. Test LLM API integration
5. Stop: `docker compose -f docker-compose.staging.yaml down`

### Production Testing (on VM):
1. Verify DNS propagation: `dig voorbeeld.soev.ai`
2. Deploy to VM following documentation
3. Verify HTTPS certificate is valid
4. Test signup/login flow
5. Test chat functionality
6. Test WebSocket connection (real-time responses)

## Performance Considerations

- PostgreSQL container should have sufficient memory (recommend 512MB minimum)
- Open WebUI container may use significant memory for embedding models
- Consider adding resource limits in production if needed:
  ```yaml
  deploy:
    resources:
      limits:
        memory: 2G
  ```

## Migration Notes

- If migrating from SQLite: Export data and reimport to PostgreSQL
- If migrating from existing deployment: Backup volumes before upgrading

## References

- Open WebUI documentation: https://docs.openwebui.com
- Caddy documentation: https://caddyserver.com/docs
- PostgreSQL Docker: https://hub.docker.com/_/postgres
