# NEO NL Migration Phases: LibreChat → Open WebUI

## Goal

Migrate NEO NL functionality to Open WebUI in testable increments.

**Key finding**: Open WebUI has native MCP client support, so we can connect directly to the existing genai-utils MCP server for document search.

---

## Phase 1: Basic Deployment & Branding

**Objective**: Get Open WebUI running with NEO NL styling and basic LLM access.

**Note**: Keep "Open WebUI" branding (required), but customize UI text and styling for NEO NL.

**Deliverables**:
- `docker-compose.neo.yaml` with Open WebUI service
- `.env.neo` with API keys and configuration
- Dutch locale as default

**Test Criteria**:
- [ ] Open WebUI accessible at configured port
- [ ] Interface displays in Dutch (`DEFAULT_LOCALE=nl-NL`)
- [ ] Can chat with `openai/gpt-oss-120b` model via Hugging Face
- [ ] User signup disabled (`ENABLE_SIGNUP=false`)
- [ ] Admin can create users via Admin Panel

**Key Environment Variables**:
```bash
# Locale & Access
DEFAULT_LOCALE=nl-NL
ENABLE_SIGNUP=false
WEBUI_AUTH=true

# LLM via Hugging Face OpenAI-compatible endpoint
OPENAI_API_BASE_URL=https://router.huggingface.co/v1
OPENAI_API_KEY=hf_...  # Hugging Face token

# Security
WEBUI_SECRET_KEY=...  # openssl rand -hex 32
```

**Note**: Using Hugging Face's OpenAI-compatible router endpoint. Additional providers can be added later via `OPENAI_API_BASE_URLS` semicolon-separated configuration.

---

## Phase 2: Weaviate RAG for File Uploads

**Objective**: Enable users to upload documents that get stored in Weaviate for RAG.

**Note**: This uses Open WebUI's native Weaviate support for user-uploaded documents. The external document collections (IAEA, ANVS, etc.) are handled separately via MCP in Phase 3.

**Deliverables**:
- Weaviate service added to docker-compose
- RAG configuration with OpenAI embeddings
- Knowledge feature enabled in UI

**Test Criteria**:
- [ ] Weaviate container healthy (`docker compose ps`)
- [ ] Can upload document via Open WebUI "Knowledge" feature
- [ ] Document appears in Weaviate collections
- [ ] Chat can query uploaded documents with RAG context
- [ ] Citations appear in responses

**Key Environment Variables**:
```bash
# Vector Database
VECTOR_DB=weaviate
WEAVIATE_HTTP_HOST=weaviate
WEAVIATE_HTTP_PORT=8080
WEAVIATE_GRPC_PORT=50051

# Embeddings
RAG_EMBEDDING_ENGINE=openai
RAG_EMBEDDING_MODEL=text-embedding-3-small
```

**Docker Compose Addition**:
```yaml
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
```

---

## Phase 3: NEO NL Pipe with MCP Integration

**Objective**: Create a Pipe function that orchestrates multi-step document search workflows using the genai-utils MCP server.

**Why Pipes instead of direct MCP Tools?**
- **Control**: Pipes let us define the reasoning flow, not just expose tools
- **Multi-step**: First discover documents, then search specific ones
- **Orchestration**: Can implement subagent patterns, loops, conditional logic
- **Custom prompts**: Embed domain-specific instructions for nuclear safety context

**Available MCP Tools** (from genai-utils):
| Tool | Description |
|------|-------------|
| `list_documents(query)` | Discover documents across all collections |
| `search_collection(query, collection)` | Search specific collection for text chunks |

**Available Collections**: `anvs`, `iaea`, `wetten_overheid`, `security`

**Deliverables**:
- `neo_nl_pipe.py` - Pipe function with orchestration logic
- MCP client integration within the Pipe
- System prompts for different use cases (nuclear safety, regulations, etc.)

**Test Criteria**:
- [ ] genai-utils MCP server running and accessible
- [ ] NEO NL Pipe visible as a "model" in Open WebUI
- [ ] Pipe correctly routes queries to appropriate collections
- [ ] Multi-step workflow works (discover → search → synthesize)
- [ ] Citations appear correctly in responses
- [ ] Custom system prompts guide the model behavior

**Pipe Architecture**:

```
┌─────────────────────────────────────────────────────────────┐
│                     NEO NL Pipe                             │
├─────────────────────────────────────────────────────────────┤
│  1. INLET: Analyze user query                               │
│     - Detect intent (search, lookup, explain)               │
│     - Extract key terms                                     │
│                                                             │
│  2. ROUTE: Determine collections                            │
│     - Nuclear safety → iaea, anvs                           │
│     - Dutch law → wetten_overheid                           │
│     - Security → security                                   │
│                                                             │
│  3. DISCOVER: Call list_documents(query)                    │
│     - Get relevant document metadata                        │
│     - Present top candidates to LLM                         │
│                                                             │
│  4. SEARCH: Call search_collection(query, collection)       │
│     - Get text chunks with citations                        │
│     - Inject as RAG context                                 │
│                                                             │
│  5. GENERATE: Call LLM with context                         │
│     - System prompt for nuclear safety domain               │
│     - User query + retrieved context                        │
│                                                             │
│  6. OUTLET: Format response                                 │
│     - Add citations/sources                                 │
│     - Format for Dutch users                                │
└─────────────────────────────────────────────────────────────┘
```

**Example Pipe Structure**:

```python
"""
title: NEO NL Document Assistant
description: Multi-step document search for nuclear safety domain
version: 0.1.0
"""

from open_webui.utils.mcp.client import MCPClient

class Pipe:
    class Valves(BaseModel):
        MCP_SERVER_URL: str = "http://genai-utils:3434/mcp"
        LLM_MODEL: str = "gpt-4o"

    def __init__(self):
        self.type = "pipe"  # or "manifold" for multiple models
        self.valves = self.Valves()

    async def pipe(self, body: dict) -> str:
        user_message = body["messages"][-1]["content"]

        # 1. Connect to MCP server
        mcp = MCPClient()
        await mcp.connect(self.valves.MCP_SERVER_URL)

        # 2. Discover relevant documents
        docs = await mcp.call_tool("list_documents", {"query": user_message})

        # 3. Search specific collections based on results
        context = await mcp.call_tool("search_collection", {
            "query": user_message,
            "collection": "iaea"  # or determined dynamically
        })

        # 4. Generate response with context
        # ... call LLM with RAG context ...

        await mcp.disconnect()
        return response
```

**System Prompts to Embed**:

| Use Case | System Prompt Focus |
|----------|---------------------|
| Nuclear Safety | IAEA guidelines, ANVS regulations, safety standards |
| Dutch Regulations | wetten.overheid.nl, legal terminology, Dutch language |
| Security | Physical/information security, compliance requirements |

---

## Phase 4: End-to-End Testing

**Objective**: Validate all functionality works together for NEO NL users.

**Test Scenarios**:

### 4.1 User Authentication
- [ ] Admin can create new user
- [ ] User can log in
- [ ] User sees Dutch interface

### 4.2 Basic Chat
- [ ] User can start conversation with `openai/gpt-oss-120b` via Hugging Face
- [ ] Responses generate correctly
- [ ] Chat history persists

### 4.3 User Document Upload (Weaviate RAG)
- [ ] Upload PDF document
- [ ] Document processed and embedded
- [ ] Query "What does my document say about X?" returns relevant content
- [ ] Citations point to uploaded document

### 4.4 External Document Search (via NEO NL Pipe)
- [ ] Select "NEO NL Assistant" as model in chat
- [ ] "Search for IAEA nuclear safety guidelines" → Pipe routes to iaea collection
- [ ] Response includes relevant content with citations
- [ ] "What does Dutch law say about X?" → Pipe routes to wetten_overheid
- [ ] Multi-collection queries work (e.g., "Compare IAEA and ANVS on topic X")

### 4.5 Pipe Orchestration
- [ ] Pipe correctly analyzes query intent
- [ ] Pipe selects appropriate collection(s)
- [ ] Multi-step flow works: discover → search → synthesize
- [ ] Citations formatted correctly with source links
- [ ] Dutch language responses when appropriate

---

## Phase Dependencies

```
Phase 1 (Basic Deployment)
    ↓
Phase 2 (Weaviate RAG)  ← User document uploads
    ↓
Phase 3 (MCP Integration) ← External document collections
    ↓
Phase 4 (Testing)        ← Validate everything
```

---

## Files to Create

| Phase | Files |
|-------|-------|
| 1 | `docker-compose.neo.yaml`, `.env.neo`, `.env.neo.example` |
| 2 | Update compose with Weaviate service |
| 3 | MCP server config (via Admin Panel or env), custom prompts |
| 4 | Test documentation |

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                        NEO NL Users                         │
└─────────────────────────────┬───────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                     Open WebUI (SvelteKit)                  │
│                                                             │
│  ┌───────────────┐  ┌───────────────┐                       │
│  │   Chat UI     │  │  Knowledge UI │                       │
│  │   (Dutch)     │  │  (File Upload)│                       │
│  └───────┬───────┘  └───────┬───────┘                       │
└──────────┼──────────────────┼───────────────────────────────┘
           │                  │
           ▼                  ▼
┌─────────────────────────────────────────────────────────────┐
│               Open WebUI Backend (FastAPI)                  │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐    │
│  │                  NEO NL Pipe                        │    │
│  │  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌────────┐  │    │
│  │  │ Analyze │→ │ Route   │→ │ Search  │→ │Respond │  │    │
│  │  │ Query   │  │ to Coll │  │ via MCP │  │+ Cite  │  │    │
│  │  └─────────┘  └─────────┘  └────┬────┘  └────────┘  │    │
│  └─────────────────────────────────┼───────────────────┘    │
│                                    │                        │
│  ┌───────────────┐  ┌──────────────┴──┐  ┌──────────────┐   │
│  │ LLM Provider  │  │   MCP Client    │  │  RAG System  │   │
│  │   (OpenAI)    │  │                 │  │  (Weaviate)  │   │
│  └───────┬───────┘  └────────┬────────┘  └──────┬───────┘   │
└──────────┼───────────────────┼──────────────────┼───────────┘
           │                   │                  │
           ▼                   ▼                  ▼
    ┌──────────────┐   ┌──────────────────┐  ┌──────────────┐
    │   OpenAI     │   │  genai-utils     │  │   Weaviate   │
    │   API        │   │  MCP Server      │  │  (User docs) │
    └──────────────┘   │  (:3434)         │  └──────────────┘
                       │                  │
                       │  ┌────────────┐  │
                       │  │ Weaviate   │  │
                       │  │ (IAEA,ANVS,│  │
                       │  │ wetten,    │  │
                       │  │ security)  │  │
                       │  └────────────┘  │
                       └──────────────────┘
```

**Data Flow**:
1. User asks question in Dutch
2. NEO NL Pipe analyzes intent and routes to appropriate collections
3. Pipe calls genai-utils MCP tools (`list_documents`, `search_collection`)
4. Retrieved context is injected into LLM prompt
5. Response generated with citations from external documents
6. User-uploaded docs (via Knowledge) use separate Weaviate RAG path

---

## Key Codebase References

**Open WebUI (for Pipe development)**:
| Feature | Location |
|---------|----------|
| MCP Client | `backend/open_webui/utils/mcp/client.py` |
| Plugin/Function loader | `backend/open_webui/utils/plugin.py` |
| Middleware (Pipe execution) | `backend/open_webui/utils/middleware.py` |
| Functions Router | `backend/open_webui/routers/functions.py` |
| Weaviate Client | `backend/open_webui/retrieval/vector/dbs/weaviate.py` |
| Config/Env Vars | `backend/open_webui/config.py` |
| Pipe example template | `src/lib/components/admin/Functions/FunctionEditor.svelte:179` |

**genai-utils (MCP Server)**:
| Feature | Location |
|---------|----------|
| MCP Server | `genai-utils/api/mcp_server.py` |
| `list_documents` tool | Line 668-801 |
| `search_collection` tool | Line 844-921 |
| Collections | anvs, iaea, wetten_overheid, security |
| Default port | 3434 |

---

*Created: 2026-01-03*
*Based on: librechat-to-openwebui-migration.md + genai-utils MCP server analysis*
