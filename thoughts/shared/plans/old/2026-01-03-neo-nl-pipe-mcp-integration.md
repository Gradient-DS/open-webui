# NEO NL Pipe with MCP Integration - Implementation Plan

## Overview

Create a NEO NL Pipe function for Open WebUI that connects to the genai-utils MCP server to search nuclear safety document collections and generate responses with citations.

## Current State Analysis

**Open WebUI Pipe System**:
- Pipes are Python classes that act as custom "models" in the UI
- Located in Admin → Functions, stored in database
- Class structure: `Valves` for config, `pipe()` method for execution
- Can return strings, generators, or iterators for streaming
- Reference: `src/lib/components/admin/Functions/FunctionEditor.svelte:176-256`

**MCP Client**:
- Available at `backend/open_webui/utils/mcp/client.py`
- Methods: `connect(url, headers)`, `call_tool(name, args)`, `disconnect()`
- Uses Streamable HTTP transport

**genai-utils MCP Server** (port 3434):
- `list_documents(query)` - Discovers documents across all collections
- `search_collection(query, collection)` - Returns text chunks with citations
- Collections: `anvs`, `iaea`, `wetten_overheid`, `security`

## Desired End State

A working NEO NL Pipe that:
1. Appears as a selectable "model" in Open WebUI chat
2. Connects to genai-utils MCP server to search document collections
3. Injects retrieved context into LLM prompts
4. Streams responses with proper citations
5. Is fully configurable via Valves (MCP URL, LLM endpoint, model, etc.)

### Verification:
- Select "NEO NL Assistant" in model dropdown
- Ask "What are IAEA safety guidelines for nuclear reactors?"
- Response streams with relevant content and citations to source documents

## What We're NOT Doing

- Complex multi-step orchestration (discover → route → search) - keeping it simple
- Using Open WebUI's internal model routing - using direct HTTP calls
- User-uploaded document RAG (handled by Phase 2 Weaviate integration)
- Multiple sub-models via manifold pattern - single Pipe for now
- OAuth authentication to MCP server - using simple bearer token or none

## Implementation Approach

Simple flow: **User Query → Search Collection → Inject Context → LLM Call → Stream Response with Citations**

The Pipe will:
1. Extract the user's question from the chat
2. Call `search_collection` on the MCP server to get relevant chunks
3. Build a RAG prompt with the retrieved context
4. Make a streaming HTTP call to the configured LLM
5. Yield response chunks back to the UI

---

## Phase 1: Basic Pipe Structure with Valves

### Overview
Create the Pipe file with proper frontmatter, Valves configuration, and basic structure.

### Changes Required:

#### 1. Create NEO NL Pipe Function
**File**: Admin → Functions → New Function (stored in database)
**Function ID**: `neo_nl_assistant`

```python
"""
title: NEO NL Document Assistant
description: Search nuclear safety documents via MCP and generate responses with citations
author: NEO NL Team
version: 0.1.0
requirements: aiohttp
"""

from pydantic import BaseModel, Field
from typing import Union, Generator, Iterator, Optional, AsyncGenerator
import json
import logging

log = logging.getLogger(__name__)


class Pipe:
    class Valves(BaseModel):
        # MCP Server Configuration
        MCP_SERVER_URL: str = Field(
            default="http://genai-utils:3434/mcp",
            description="URL of the genai-utils MCP server"
        )

        # LLM Configuration
        LLM_API_BASE_URL: str = Field(
            default="https://router.huggingface.co/v1",
            description="Base URL for the LLM API (OpenAI-compatible)"
        )
        LLM_API_KEY: str = Field(
            default="",
            description="API key for the LLM provider"
        )
        LLM_MODEL: str = Field(
            default="openai/gpt-oss-120b",
            description="Model ID to use for generation"
        )

        # Search Configuration
        DEFAULT_COLLECTION: str = Field(
            default="iaea",
            description="Default collection to search (anvs, iaea, wetten_overheid, security)"
        )
        MAX_CONTEXT_CHUNKS: int = Field(
            default=5,
            description="Maximum number of context chunks to include"
        )

    def __init__(self):
        self.valves = self.Valves()

    async def pipe(
        self,
        body: dict,
        __event_emitter__=None,
    ) -> AsyncGenerator[str, None]:
        """Main pipe execution - placeholder for Phase 1."""

        # Extract user message
        messages = body.get("messages", [])
        if not messages:
            yield "No messages provided."
            return

        user_message = messages[-1].get("content", "")

        # Phase 1: Just echo back to verify Pipe works
        yield f"[NEO NL Assistant] Received query: {user_message}\n\n"
        yield "Phase 1 complete - Pipe structure working!"
```

### Success Criteria:

#### Automated Verification:
- [x] Function saves without syntax errors in Admin → Functions
- [x] No import errors when loading the function

#### Manual Verification:
- [ ] "NEO NL Document Assistant" appears in model selector dropdown
- [ ] Selecting it and sending a message returns the echo response
- [ ] Response streams (not all at once)

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation that the Pipe appears and responds correctly.

---

## Phase 2: MCP Connection and Search

### Overview
Add MCP client integration to search the document collections.

### Changes Required:

#### 1. Add MCP Search Logic
**File**: Update the Pipe function in Admin → Functions

```python
"""
title: NEO NL Document Assistant
description: Search nuclear safety documents via MCP and generate responses with citations
author: NEO NL Team
version: 0.1.0
requirements: aiohttp
"""

from pydantic import BaseModel, Field
from typing import Union, Generator, Iterator, Optional, AsyncGenerator
import json
import logging

# Import MCP client from Open WebUI
from open_webui.utils.mcp.client import MCPClient

log = logging.getLogger(__name__)


class Pipe:
    class Valves(BaseModel):
        # MCP Server Configuration
        MCP_SERVER_URL: str = Field(
            default="http://genai-utils:3434/mcp",
            description="URL of the genai-utils MCP server"
        )

        # LLM Configuration
        LLM_API_BASE_URL: str = Field(
            default="https://router.huggingface.co/v1",
            description="Base URL for the LLM API (OpenAI-compatible)"
        )
        LLM_API_KEY: str = Field(
            default="",
            description="API key for the LLM provider"
        )
        LLM_MODEL: str = Field(
            default="openai/gpt-oss-120b",
            description="Model ID to use for generation"
        )

        # Search Configuration
        DEFAULT_COLLECTION: str = Field(
            default="iaea",
            description="Default collection to search (anvs, iaea, wetten_overheid, security)"
        )
        MAX_CONTEXT_CHUNKS: int = Field(
            default=5,
            description="Maximum number of context chunks to include"
        )

    def __init__(self):
        self.valves = self.Valves()

    async def _search_documents(self, query: str, collection: str) -> list:
        """Search documents via MCP server."""
        mcp_client = MCPClient()

        try:
            await mcp_client.connect(self.valves.MCP_SERVER_URL)

            # Call search_collection tool
            result = await mcp_client.call_tool(
                "search_collection",
                {"query": query, "collection": collection}
            )

            return result if result else []

        except Exception as e:
            log.error(f"MCP search error: {e}")
            return []
        finally:
            try:
                await mcp_client.disconnect()
            except Exception:
                pass

    def _extract_text_from_mcp_result(self, result: list) -> list[dict]:
        """Extract text content and metadata from MCP result."""
        chunks = []

        for item in result:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text", "")
                # Parse the text content - MCP returns formatted text
                chunks.append({
                    "content": text,
                    "source": "MCP Search Result"
                })

        return chunks[:self.valves.MAX_CONTEXT_CHUNKS]

    async def pipe(
        self,
        body: dict,
        __event_emitter__=None,
    ) -> AsyncGenerator[str, None]:
        """Main pipe execution."""

        # Extract user message
        messages = body.get("messages", [])
        if not messages:
            yield "No messages provided."
            return

        user_message = messages[-1].get("content", "")

        # Emit status: searching
        if __event_emitter__:
            await __event_emitter__({
                "type": "status",
                "data": {"description": "Searching documents...", "done": False}
            })

        # Search documents
        search_results = await self._search_documents(
            query=user_message,
            collection=self.valves.DEFAULT_COLLECTION
        )

        chunks = self._extract_text_from_mcp_result(search_results)

        # Emit status: done searching
        if __event_emitter__:
            await __event_emitter__({
                "type": "status",
                "data": {"description": f"Found {len(chunks)} relevant chunks", "done": True}
            })

        # Phase 2: Return search results for verification
        yield f"## Search Results for: {user_message}\n\n"
        yield f"Collection: {self.valves.DEFAULT_COLLECTION}\n"
        yield f"Found {len(chunks)} chunks:\n\n"

        for i, chunk in enumerate(chunks, 1):
            yield f"### Chunk {i}\n"
            yield f"{chunk['content'][:500]}...\n\n"
```

### Success Criteria:

#### Automated Verification:
- [x] Function saves without errors
- [x] MCP client import resolves correctly

#### Manual Verification:
- [ ] genai-utils MCP server is running (`docker compose ps` shows healthy)
- [ ] Sending a query returns actual search results from the MCP server
- [ ] Status message "Searching documents..." appears during search
- [ ] Results show content from the configured collection

**Implementation Note**: Ensure the genai-utils MCP server is running and accessible at the configured URL before testing.

---

## Phase 3: LLM Integration with Streaming

### Overview
Add the LLM call with RAG context injection and streaming response.

### Changes Required:

#### 1. Complete Pipe with LLM Call
**File**: Update the Pipe function in Admin → Functions

```python
"""
title: NEO NL Document Assistant
description: Search nuclear safety documents via MCP and generate responses with citations
author: NEO NL Team
version: 0.1.0
requirements: aiohttp
"""

from pydantic import BaseModel, Field
from typing import Optional, AsyncGenerator
import json
import logging
import aiohttp

# Import MCP client from Open WebUI
from open_webui.utils.mcp.client import MCPClient

log = logging.getLogger(__name__)


# System prompt for nuclear safety domain
SYSTEM_PROMPT = """Je bent een deskundige assistent voor nucleaire veiligheid die vragen beantwoordt op basis van officiële documenten van het IAEA, ANVS en Nederlandse wetgeving.

Richtlijnen:
- Beantwoord vragen in het Nederlands, tenzij anders gevraagd
- Baseer je antwoorden uitsluitend op de verstrekte context
- Citeer bronnen met [1], [2], etc. wanneer je informatie uit de context gebruikt
- Als de context onvoldoende informatie bevat, geef dit duidelijk aan
- Wees nauwkeurig en objectief bij het bespreken van veiligheidsvoorschriften

Context uit documenten:
{context}

Beantwoord de vraag van de gebruiker op basis van bovenstaande context."""


class Pipe:
    class Valves(BaseModel):
        # MCP Server Configuration
        MCP_SERVER_URL: str = Field(
            default="http://genai-utils:3434/mcp",
            description="URL of the genai-utils MCP server"
        )

        # LLM Configuration
        LLM_API_BASE_URL: str = Field(
            default="https://router.huggingface.co/v1",
            description="Base URL for the LLM API (OpenAI-compatible)"
        )
        LLM_API_KEY: str = Field(
            default="",
            description="API key for the LLM provider"
        )
        LLM_MODEL: str = Field(
            default="openai/gpt-oss-120b",
            description="Model ID to use for generation"
        )

        # Search Configuration
        DEFAULT_COLLECTION: str = Field(
            default="iaea",
            description="Default collection to search (anvs, iaea, wetten_overheid, security)"
        )
        MAX_CONTEXT_CHUNKS: int = Field(
            default=5,
            description="Maximum number of context chunks to include"
        )

        # Generation Configuration
        TEMPERATURE: float = Field(
            default=0.7,
            description="Temperature for LLM generation"
        )
        MAX_TOKENS: int = Field(
            default=2048,
            description="Maximum tokens in response"
        )

    def __init__(self):
        self.valves = self.Valves()

    async def _search_documents(self, query: str, collection: str) -> list:
        """Search documents via MCP server."""
        mcp_client = MCPClient()

        try:
            await mcp_client.connect(self.valves.MCP_SERVER_URL)

            result = await mcp_client.call_tool(
                "search_collection",
                {"query": query, "collection": collection}
            )

            return result if result else []

        except Exception as e:
            log.error(f"MCP search error: {e}")
            return []
        finally:
            try:
                await mcp_client.disconnect()
            except Exception:
                pass

    def _extract_text_from_mcp_result(self, result: list) -> list[dict]:
        """Extract text content and metadata from MCP result."""
        chunks = []

        for item in result:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text", "")
                chunks.append({
                    "content": text,
                    "source": "MCP Search Result"
                })

        return chunks[:self.valves.MAX_CONTEXT_CHUNKS]

    def _build_context_string(self, chunks: list[dict]) -> str:
        """Build context string from chunks with source markers."""
        if not chunks:
            return "Geen relevante documenten gevonden."

        context_parts = []
        for i, chunk in enumerate(chunks, 1):
            context_parts.append(f"[{i}] {chunk['content']}")

        return "\n\n".join(context_parts)

    async def _stream_llm_response(
        self,
        messages: list[dict],
    ) -> AsyncGenerator[str, None]:
        """Stream response from LLM API."""

        headers = {
            "Content-Type": "application/json",
        }
        if self.valves.LLM_API_KEY:
            headers["Authorization"] = f"Bearer {self.valves.LLM_API_KEY}"

        payload = {
            "model": self.valves.LLM_MODEL,
            "messages": messages,
            "stream": True,
            "temperature": self.valves.TEMPERATURE,
            "max_tokens": self.valves.MAX_TOKENS,
        }

        url = f"{self.valves.LLM_API_BASE_URL}/chat/completions"

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as response:
                if response.status != 200:
                    error_text = await response.text()
                    yield f"Error from LLM API: {response.status} - {error_text}"
                    return

                async for line in response.content:
                    line = line.decode("utf-8").strip()

                    if not line or not line.startswith("data: "):
                        continue

                    data_str = line[6:]  # Remove "data: " prefix

                    if data_str == "[DONE]":
                        break

                    try:
                        data = json.loads(data_str)
                        delta = data.get("choices", [{}])[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            yield content
                    except json.JSONDecodeError:
                        continue

    async def pipe(
        self,
        body: dict,
        __event_emitter__=None,
    ) -> AsyncGenerator[str, None]:
        """Main pipe execution."""

        # Extract user message
        messages = body.get("messages", [])
        if not messages:
            yield "No messages provided."
            return

        user_message = messages[-1].get("content", "")

        # Emit status: searching
        if __event_emitter__:
            await __event_emitter__({
                "type": "status",
                "data": {"description": "Zoeken in documenten...", "done": False}
            })

        # Search documents
        search_results = await self._search_documents(
            query=user_message,
            collection=self.valves.DEFAULT_COLLECTION
        )

        chunks = self._extract_text_from_mcp_result(search_results)

        # Emit status: generating
        if __event_emitter__:
            await __event_emitter__({
                "type": "status",
                "data": {"description": f"Genereren van antwoord ({len(chunks)} bronnen)...", "done": False}
            })

        # Build context and messages
        context_string = self._build_context_string(chunks)
        system_message = SYSTEM_PROMPT.format(context=context_string)

        llm_messages = [
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_message}
        ]

        # Stream LLM response
        async for chunk in self._stream_llm_response(llm_messages):
            yield chunk

        # Emit status: done
        if __event_emitter__:
            await __event_emitter__({
                "type": "status",
                "data": {"description": "Voltooid", "done": True}
            })
```

### Success Criteria:

#### Automated Verification:
- [x] Function saves without errors
- [x] aiohttp import works

#### Manual Verification:
- [ ] Query returns a streaming response (text appears incrementally)
- [ ] Response is in Dutch (as per system prompt)
- [ ] Response references the retrieved context with [1], [2] style citations
- [ ] Status messages update during processing (Zoeken → Genereren → Voltooid)

**Implementation Note**: Configure the LLM API key in the Valves before testing.

---

## Phase 4: Citation Events and Collection Routing

### Overview
Add source/citation events for UI display and basic collection routing based on query keywords.

### Changes Required:

#### 1. Add Citation Events and Collection Detection
**File**: Update the Pipe function in Admin → Functions

Add after the LLM streaming completes:

```python
    def _detect_collection(self, query: str) -> str:
        """Detect appropriate collection based on query keywords."""
        query_lower = query.lower()

        # Dutch law keywords
        if any(kw in query_lower for kw in ["wet", "regel", "voorschrift", "besluit", "verordening", "juridisch"]):
            return "wetten_overheid"

        # Security keywords
        if any(kw in query_lower for kw in ["beveilig", "security", "fysiek", "toegang", "informatie"]):
            return "security"

        # ANVS (Dutch Nuclear Safety Authority) keywords
        if any(kw in query_lower for kw in ["anvs", "nederland", "vergunning", "toezicht"]):
            return "anvs"

        # Default to IAEA for general nuclear safety
        return "iaea"

    async def pipe(
        self,
        body: dict,
        __event_emitter__=None,
    ) -> AsyncGenerator[str, None]:
        """Main pipe execution."""

        # Extract user message
        messages = body.get("messages", [])
        if not messages:
            yield "No messages provided."
            return

        user_message = messages[-1].get("content", "")

        # Detect collection based on query
        collection = self._detect_collection(user_message)

        # Emit status: searching
        if __event_emitter__:
            await __event_emitter__({
                "type": "status",
                "data": {"description": f"Zoeken in {collection}...", "done": False}
            })

        # Search documents
        search_results = await self._search_documents(
            query=user_message,
            collection=collection
        )

        chunks = self._extract_text_from_mcp_result(search_results)

        # Emit sources for citation display
        if __event_emitter__ and chunks:
            for i, chunk in enumerate(chunks, 1):
                await __event_emitter__({
                    "type": "source",
                    "data": {
                        "source": {
                            "name": f"[{i}] {collection.upper()} Document",
                        },
                        "document": [chunk["content"][:500]],
                        "metadata": [{"collection": collection}]
                    }
                })

        # ... rest of pipe implementation (same as Phase 3)
```

### Success Criteria:

#### Automated Verification:
- [ ] Function saves without errors

#### Manual Verification:
- [ ] Query about "Nederlandse wetgeving" routes to `wetten_overheid` collection
- [ ] Query about "IAEA safety standards" routes to `iaea` collection
- [ ] Query about "beveiliging" routes to `security` collection
- [ ] Citations appear in the UI (expandable sources panel)
- [ ] Status message shows which collection is being searched

**Implementation Note**: After completing this phase, pause for manual testing of the collection routing logic.

---

## Testing Strategy

### Unit Tests:
- Not applicable for Pipe functions (stored in database, not files)

### Integration Tests:
- Verify MCP server connectivity
- Verify LLM API connectivity
- End-to-end query flow

### Manual Testing Steps:

1. **Basic Functionality**:
   - [ ] Create new Function in Admin → Functions with ID `neo_nl_assistant`
   - [ ] Paste the Pipe code
   - [ ] Save and verify no errors
   - [ ] Select "NEO NL Document Assistant" in chat model selector
   - [ ] Send test message: "Wat zijn de IAEA veiligheidsrichtlijnen?"

2. **Collection Routing**:
   - [ ] Query: "Nederlandse nucleaire wetgeving" → should search `wetten_overheid`
   - [ ] Query: "IAEA safety standards" → should search `iaea`
   - [ ] Query: "Fysieke beveiliging kerncentrale" → should search `security`

3. **Streaming & Citations**:
   - [ ] Verify response streams incrementally
   - [ ] Verify [1], [2] citations appear in response
   - [ ] Verify sources panel shows retrieved documents

4. **Error Handling**:
   - [ ] Stop MCP server → verify graceful error message
   - [ ] Invalid LLM API key → verify error handling

---

## Performance Considerations

- MCP connection is established per request (no connection pooling)
- Consider caching MCP client if latency becomes an issue
- Context string size limited by `MAX_CONTEXT_CHUNKS` to control token usage

---

## Migration Notes

- No database migrations required
- Pipe is stored in Open WebUI's Functions table
- Configuration via Valves in Admin UI

---

## References

- Research document: `thoughts/shared/research/neo-nl-migration-phases.md`
- Open WebUI Pipe template: `src/lib/components/admin/Functions/FunctionEditor.svelte:176-256`
- MCP Client: `backend/open_webui/utils/mcp/client.py`
- genai-utils MCP Server: `genai-utils/api/mcp_server.py:668-921`

---

*Created: 2026-01-03*
*Based on: Phase 3 of NEO NL Migration Phases research document*
