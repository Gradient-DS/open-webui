# NEO NL Phase 1: Basic Deployment & Branding - Implementation Plan

## Overview

Deploy Open WebUI with NEO NL configuration: Dutch locale, disabled public signup, and LLM access via Hugging Face's OpenAI-compatible endpoint. This creates the foundation for NEO NL users to interact with LLMs before adding RAG capabilities in later phases.

## Current State Analysis

Open WebUI is a self-hosted AI platform with comprehensive configuration options:
- Docker deployment using `docker-compose.yaml` with volume persistence
- Environment-based configuration with database persistence (`PersistentConfig`)
- Dutch locale (`nl-NL`) fully translated and available
- Authentication system with signup controls and admin user management
- OpenAI-compatible API support with custom base URL configuration

### Key Discoveries

| Finding | Location | Impact |
|---------|----------|--------|
| Custom base URL support | `config.py:1043` | Set `OPENAI_API_BASE_URL` for Hugging Face |
| Dutch locale available | `src/lib/i18n/locales/nl-NL/` | Set `DEFAULT_LOCALE=nl-NL` |
| First user auto-admin | `routers/auths.py:677` | No need to configure admin separately |
| Auto-disable signup | `routers/auths.py:729-731` | Signup disabled after first user anyway |
| Secret key auto-gen | `__init__.py:39-47` | Can leave empty for auto-generation |

**Note**: Using Hugging Face's OpenAI-compatible router endpoint (`https://router.huggingface.co/v1`). Additional providers can be added later via `OPENAI_API_BASE_URLS` semicolon-separated configuration.

## Desired End State

After Phase 1 completion:

1. **Docker deployment running** at configured port with persistent data
2. **Dutch interface** as default language for all users
3. **No public signup** - only admin can create new users
4. **LLM access** via Hugging Face endpoint (`openai/gpt-oss-120b` model)
5. **Admin user** created and able to manage users/settings

### Verification

```bash
# Containers running
docker compose -f docker-compose.neo.yaml ps

# Interface accessible
curl -s http://localhost:3000 | grep -q "Open WebUI"

# API responsive
curl -s http://localhost:3000/api/config | jq '.default_locale'
# Should return: "nl-NL"
```

## What We're NOT Doing

- **Weaviate/RAG setup** - Phase 2
- **MCP integration** - Phase 3
- **Custom Pipes** - Phase 3
- **Custom branding/logos** - Optional, post-deployment
- **HTTPS/SSL configuration** - Infrastructure concern, handled by reverse proxy
- **Backup/restore procedures** - Operational concern

## Implementation Approach

Create three files in the repository root:
1. `docker-compose.neo.yaml` - Minimal compose file for NEO NL (no Ollama)
2. `.env.neo.example` - Template with placeholder values
3. `.env.neo` - Actual configuration (gitignored)

---

## Phase 1.1: Create Docker Compose File

### Overview

Create a dedicated compose file for NEO NL deployment without Ollama (since we're using cloud LLMs).

### Changes Required

**File**: `docker-compose.neo.yaml` (new file)

```yaml
# NEO NL Open WebUI Deployment
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
    extra_hosts:
      - host.docker.internal:host-gateway
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s

volumes:
  neo-nl-data: {}
```

### Success Criteria

#### Automated Verification
- [x] File created at `docker-compose.neo.yaml`
- [x] YAML syntax valid: `docker compose -f docker-compose.neo.yaml config`

---

## Phase 1.2: Create Environment Template

### Overview

Create `.env.neo.example` as a template showing all required configuration.

### Changes Required

**File**: `.env.neo.example` (new file)

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
# TELEMETRY (disabled for privacy)
# ------------------------------------------------------------
SCARF_NO_ANALYTICS=true
DO_NOT_TRACK=true
ANONYMIZED_TELEMETRY=false
```

### Success Criteria

#### Automated Verification
- [x] File created at `.env.neo.example`
- [x] File contains no actual secrets (only placeholders)

---

## Phase 1.3: Create Actual Environment File

### Overview

Create `.env.neo` with real configuration values. This file should be gitignored.

### Changes Required

**File**: `.env.neo` (new file, gitignored)

Copy from `.env.neo.example` and fill in actual values:

```bash
# Copy template
cp .env.neo.example .env.neo

# Edit with actual values
# OPENAI_API_KEY=hf_actual-huggingface-token
```

**File**: `.gitignore` (update)

Add if not already present:
```
.env.neo
```

### Success Criteria

#### Automated Verification
- [x] File created at `.env.neo`
- [x] `.env.neo` is gitignored: `git check-ignore .env.neo` returns the path

#### Manual Verification
- [ ] Hugging Face token is valid and has "Make calls to Inference Providers" permission

---

## Phase 1.4: Deploy and Verify

### Overview

Start the deployment and create the initial admin user.

### Deployment Steps

```bash
# 1. Start the container
docker compose -f docker-compose.neo.yaml up -d

# 2. Check container health
docker compose -f docker-compose.neo.yaml ps
docker compose -f docker-compose.neo.yaml logs -f open-webui

# 3. Wait for healthy status (may take 30-60 seconds)
docker compose -f docker-compose.neo.yaml ps --format "table {{.Name}}\t{{.Status}}"
```

### Initial Admin Setup

1. Navigate to `http://localhost:3000` (or configured port)
2. Click "Sign up" (available only for first user due to `ENABLE_INITIAL_ADMIN_SIGNUP=true`)
3. Create admin account with email and password
4. After signup, public signup is automatically disabled

### Success Criteria

#### Automated Verification
- [ ] Container running: `docker compose -f docker-compose.neo.yaml ps | grep -q "Up"`
- [ ] Health check passing: `curl -f http://localhost:3000/health`
- [ ] API responding: `curl -s http://localhost:3000/api/config | jq -e '.default_locale == "nl-NL"'`
- [ ] Dutch locale configured: response shows `"nl-NL"`

#### Manual Verification
- [ ] Open WebUI accessible at `http://localhost:3000`
- [ ] Interface displays in Dutch by default
- [ ] Admin account created successfully
- [ ] After admin creation, signup page shows "Access Prohibited" for new visitors
- [ ] Can start a chat conversation
- [ ] Hugging Face models appear in model selector (including `openai/gpt-oss-120b`)
- [ ] Can send a message to `openai/gpt-oss-120b` and receive a response

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the deployment is working correctly.

---

## Phase 1.5: Admin User Management

### Overview

Verify admin can create users via the Admin Panel.

### Steps

1. Log in as admin
2. Navigate to Admin Panel (gear icon → Admin Settings)
3. Go to Users tab
4. Click "Add User" button
5. Create a test user with:
   - Email: `test@neo-nl.local`
   - Password: (set a password)
   - Role: `user`

### Success Criteria

#### Manual Verification
- [ ] Admin can access Admin Panel
- [ ] "Add User" button visible in Users tab
- [ ] Can create new user with email/password
- [ ] New user can log in with created credentials
- [ ] New user sees Dutch interface
- [ ] New user cannot access Admin Panel

---

## Testing Strategy

### Automated Tests

```bash
# Container health
docker compose -f docker-compose.neo.yaml ps | grep -q "healthy"

# API accessibility
curl -f http://localhost:3000/api/config

# Locale configuration
curl -s http://localhost:3000/api/config | jq -e '.default_locale == "nl-NL"'

# Signup disabled (should return 403 after first user)
# Note: Only run after initial admin is created
curl -s -X POST http://localhost:3000/api/v1/auths/signup \
  -H "Content-Type: application/json" \
  -d '{"email":"test@test.com","password":"test123","name":"Test"}' \
  | jq -e '.detail == "ACCESS_PROHIBITED"'
```

### Manual Testing Steps

1. **Fresh browser session**: Open `http://localhost:3000` in incognito
2. **Language check**: Verify Dutch text appears (e.g., "Welkom", "Inloggen")
3. **Chat functionality**: Send "Hello, who are you?" to `openai/gpt-oss-120b` and verify response
4. **Model switching**: Try other available Hugging Face models if listed
5. **Settings persistence**: Change a setting, refresh, verify it persists

---

## Troubleshooting

### Container won't start

```bash
# Check logs
docker compose -f docker-compose.neo.yaml logs open-webui

# Common issues:
# - Port already in use: change OPEN_WEBUI_PORT in .env.neo
# - Volume permissions: docker volume rm neo-nl-data (WARNING: deletes data)
```

### Models not appearing

```bash
# Check API key configuration
docker compose -f docker-compose.neo.yaml exec open-webui env | grep OPENAI

# Test Hugging Face endpoint directly
curl -H "Authorization: Bearer $HF_TOKEN" https://router.huggingface.co/v1/models

# Test chat completion
curl -X POST https://router.huggingface.co/v1/chat/completions \
  -H "Authorization: Bearer $HF_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model": "openai/gpt-oss-120b", "messages": [{"role": "user", "content": "Hello"}]}'
```

### Dutch not default

```bash
# Verify DEFAULT_LOCALE is set
docker compose -f docker-compose.neo.yaml exec open-webui env | grep DEFAULT_LOCALE

# Check config API
curl -s http://localhost:3000/api/config | jq '.default_locale'

# Clear browser localStorage and refresh
```

---

## Files to Create

| File | Purpose | Commit |
|------|---------|--------|
| `docker-compose.neo.yaml` | NEO NL deployment configuration | Yes |
| `.env.neo.example` | Configuration template | Yes |
| `.env.neo` | Actual configuration with secrets | No (gitignored) |

---

## References

- Original research: `thoughts/shared/research/neo-nl-migration-phases.md`
- Docker patterns: `docker-compose.yaml` (existing base file)
- Environment config: `backend/open_webui/config.py:1042-1153`
- Auth system: `backend/open_webui/routers/auths.py:642-889`
- Locale setup: `src/lib/i18n/locales/nl-NL/translation.json`

---

*Created: 2026-01-03*
*Phase: 1 of 4 (NEO NL Migration)*
