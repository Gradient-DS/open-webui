# NEO NL Agent: LangGraph Multi-Agent RAG Pipe

## Overview

Implement a new pipe `neo_nl_agent.py` that uses LangGraph's StateGraph to orchestrate a multi-agent RAG workflow. This pipe runs alongside the existing `neo_nl_assistant.py` and provides enhanced query handling through:

- **Query type classification** (factual, exploratory, deep_dive, comparative)
- **Conditional routing** to different retrieval strategies
- **Context summarization** to compress retrieved chunks before synthesis
- **All three MCP tools**: `list_documents`, `search_collection`, `read_document`

## Current State Analysis

### Existing Implementation (`neo_nl_assistant.py:263-336`)
- Single-step RAG: search one collection → generate response
- No query classification
- No discovery across collections
- No context compression
- Simple but limited for broad queries

### Available MCP Tools (genai-utils `mcp_server.py`)
| Tool | Purpose | Returns |
|------|---------|---------|
| `list_documents(query)` | Discovery across all collections | Document metadata grouped by collection |
| `search_collection(query, collection)` | Search within one collection | Chunks with file citations |
| `read_document(query, doc_id, collection)` | Deep read specific document | Chunks from single document with citations |

### Key Discoveries
- LangChain is available (`backend/requirements.txt:48-51`)
- LangGraph needs to be added as a pipe requirement
- Pipes can specify requirements in frontmatter (installed per-pipe)
- Open WebUI's `generate_chat_completion()` must be used for all LLM calls

## Desired End State

A new pipe `neo_nl_agent.py` that:
1. Classifies incoming queries into types (factual/exploratory/deep_dive/comparative)
2. Routes to appropriate retrieval strategy based on query type
3. Uses discovery for broad queries, direct search for specific ones
4. Summarizes retrieved context before final synthesis
5. Emits sources and fun status messages throughout the workflow
6. Deploys via `bootstrap_functions.py` alongside `neo_nl_assistant`

### Verification
- Unit test query classification with sample queries
- Test each query type routes to correct flow
- Verify sources appear correctly in Open WebUI citations
- Compare response quality with `neo_nl_assistant` on test queries

## What We're NOT Doing

- **Not replacing `neo_nl_assistant`** - runs alongside as alternative
- **Not using separate models** - single model for all LLM calls (gpt-oss-120b via Open WebUI)
- **Not implementing iterative refinement** - single pass through the graph
- **Not adding caching** - can be added later if needed
- **Not using LangSmith** - keep it simple, use logging

## Implementation Approach

Use LangGraph's StateGraph with explicit conditional routing. Each node is a Python async function that:
1. Receives the current state
2. Performs one focused task (classify, search, summarize, etc.)
3. Returns state updates (not full state)

Wrap Open WebUI's `generate_chat_completion()` in a simple async helper (not full LangChain BaseChatModel - that adds complexity without benefit for our use case).

---

## Phase 1: Core Infrastructure

### Overview
Set up the basic pipe structure, state definitions, and LLM helper function.

### Changes Required

#### 1. Create new pipe file
**File**: `scripts/pipes/neo_nl_agent.py`

```python
"""
title: NEO NL Agent
description: Multi-agent RAG with query routing and document discovery
author: NEO NL Team
version: 1.0.0
requirements: langgraph
"""

import json
import asyncio
import random
import logging
from typing import TypedDict, Literal, Optional, List, Any, AsyncGenerator

from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, END

from starlette.responses import StreamingResponse
from open_webui.utils.chat import generate_chat_completion
from open_webui.models.users import Users
from open_webui.utils.mcp.client import MCPClient

log = logging.getLogger(__name__)


# =============================================================================
# STATE DEFINITION
# =============================================================================

class GraphState(TypedDict):
    """State that flows through the LangGraph workflow"""
    # Input
    query: str
    messages: list[dict]

    # Routing
    query_type: Literal["factual", "exploratory", "deep_dive", "comparative"]
    target_collections: list[str]

    # Retrieved content
    discovered_docs: list[dict]
    retrieved_chunks: list[dict]
    sources: list[dict]
    document_content: Optional[str]

    # Summarized
    summarized_facts: list[dict]

    # Context for Open WebUI (passed through, not modified)
    _request: Any
    _user: dict
    _event_emitter: Any
    _valves: Any


# =============================================================================
# STATUS MESSAGES (Fun Nuclear Edition)
# =============================================================================

STATUS_MESSAGES = {
    "routing": [
        "Analyzing your question...",
        "Decoding your nuclear inquiry...",
        "Quantum-analyzing query parameters...",
    ],
    "discovering": [
        "Discovering relevant documents...",
        "Scanning the nuclear knowledge vault...",
        "Exploring all collections...",
    ],
    "retrieving": [
        "Searching documents...",
        "Retrieving nuclear intelligence...",
        "Mining the document reactor...",
    ],
    "reading": [
        "Reading document in detail...",
        "Deep-diving into the source...",
        "Absorbing nuclear knowledge...",
    ],
    "summarizing": [
        "Analyzing findings...",
        "Extracting key facts...",
        "Distilling nuclear wisdom...",
    ],
    "synthesizing": [
        "Generating response...",
        "Synthesizing your answer...",
        "Fusing knowledge into response...",
    ],
    "done": [
        "Complete",
        "Ready to radiate knowledge",
        "Nuclear answer delivered",
    ],
}


async def emit_status(emitter, phase: str, done: bool = False):
    """Emit fun status message to UI."""
    if not emitter:
        return
    messages = STATUS_MESSAGES.get(phase, ["Processing..."])
    message = random.choice(messages)
    await emitter({"type": "status", "data": {"description": message, "done": done}})


# =============================================================================
# LLM HELPER
# =============================================================================

async def call_llm(
    messages: list[dict],
    request: Any,
    user: dict,
    model_id: str,
    stream: bool = False
) -> str | AsyncGenerator[str, None]:
    """Call LLM via Open WebUI's internal routing."""
    user_obj = Users.get_user_by_id(user.get("id"))
    if not user_obj:
        if stream:
            async def error_gen():
                yield "Error: User not found"
            return error_gen()
        return "Error: User not found"

    payload = {"model": model_id, "messages": messages, "stream": stream}

    response = await generate_chat_completion(
        request=request,
        form_data=payload,
        user=user_obj,
        bypass_filter=True,
    )

    if stream:
        async def stream_response():
            if isinstance(response, StreamingResponse):
                async for chunk in response.body_iterator:
                    chunk_str = chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
                    for line in chunk_str.split("\n"):
                        line = line.strip()
                        if line.startswith("data: ") and line != "data: [DONE]":
                            try:
                                data = json.loads(line[6:])
                                content = data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                                if content:
                                    yield content
                            except json.JSONDecodeError:
                                continue
            elif isinstance(response, dict):
                yield response.get("choices", [{}])[0].get("message", {}).get("content", "")
        return stream_response()
    else:
        if isinstance(response, dict):
            return response.get("choices", [{}])[0].get("message", {}).get("content", "")
        return str(response)
```

### Success Criteria

#### Automated Verification:
- [x] File exists at `scripts/pipes/neo_nl_agent.py`
- [x] Python syntax is valid: `python -m py_compile scripts/pipes/neo_nl_agent.py`
- [ ] Imports resolve (may need to run within Open WebUI context)

#### Manual Verification:
- [ ] None for this phase

---

## Phase 2: Graph Nodes

### Overview
Implement all the node functions: router, discovery, retrieval (sequential + parallel), reader, summarizer, synthesizer.

### Changes Required

#### 1. Add prompts
**File**: `scripts/pipes/neo_nl_agent.py` (append after STATUS_MESSAGES)

```python
# =============================================================================
# PROMPTS
# =============================================================================

ROUTER_PROMPT = """Classify this query and identify collections to search.

Query types:
- factual: Asks for specific information (e.g., "What is IAEA standard X?")
- exploratory: Asks what's available (e.g., "What documents discuss nuclear safety?")
- deep_dive: Requests detailed explanation of specific topic/document
- comparative: Compares multiple sources (e.g., "Compare IAEA and ANVS on X")

Collections: anvs, iaea, wetten_overheid, security

Query: {query}

Respond ONLY with valid JSON:
{{"type": "factual|exploratory|deep_dive|comparative", "collections": ["collection1", ...]}}"""

SUMMARIZER_PROMPT = """Extract key facts to answer: {query}

Documents:
{chunks}

Rules:
1. Only facts relevant to the query
2. Each fact must have a citation
3. Maximum 5 facts
4. If no relevant facts, return empty list

Output JSON: [{{"fact": "...", "source": "collection:doc_id", "title": "..."}}]"""

SYSTEM_PROMPT = """Je bent de NEO NL assistent voor kernenergie in Nederland.

BESCHIKBARE INFORMATIE:
{context}

INSTRUCTIES:
- Antwoord in het Nederlands
- Citeer bronnen met [1], [2], etc. wanneer je informatie uit de context gebruikt
- Als informatie ontbreekt, zeg dit eerlijk
- Maximaal 400 woorden
- Wees specifiek en concreet"""
```

#### 2. Add MCP helper functions
**File**: `scripts/pipes/neo_nl_agent.py` (append)

```python
# =============================================================================
# MCP HELPERS
# =============================================================================

async def mcp_list_documents(url: str, query: str) -> list[dict]:
    """Call list_documents MCP tool."""
    client = MCPClient()
    try:
        await client.connect(url)
        result = await client.call_tool("list_documents", {"query": query})
        return parse_discovery_result(result)
    except Exception as e:
        log.error(f"MCP list_documents error: {e}")
        return []
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


async def mcp_search_collection(url: str, query: str, collection: str) -> tuple[list[dict], list[dict]]:
    """Call search_collection MCP tool. Returns (chunks, sources)."""
    client = MCPClient()
    try:
        await client.connect(url)
        result = await client.call_tool("search_collection", {
            "query": query,
            "collection": collection
        })
        return parse_search_result(result, collection)
    except Exception as e:
        log.error(f"MCP search_collection error: {e}")
        return [], []
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


async def mcp_read_document(url: str, query: str, doc_id: str, collection: str) -> tuple[str, list[dict]]:
    """Call read_document MCP tool. Returns (content, sources)."""
    client = MCPClient()
    try:
        await client.connect(url)
        result = await client.call_tool("read_document", {
            "query": query,
            "doc_id": doc_id,
            "collection": collection
        })
        return parse_read_result(result)
    except Exception as e:
        log.error(f"MCP read_document error: {e}")
        return "", []
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


def parse_discovery_result(result: list) -> list[dict]:
    """Parse list_documents result into document metadata."""
    docs = []
    if not result:
        return docs

    for item in result:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            # Parse the formatted text output
            text = item.get("text", "")
            current_collection = ""
            for line in text.split("\n"):
                line = line.strip()
                if line.startswith("## "):
                    # Collection header like "## ANVS (3 results)"
                    current_collection = line.split()[1].lower() if len(line.split()) > 1 else ""
                elif line.startswith("- Doc ID:"):
                    doc_id = line.replace("- Doc ID:", "").strip()
                    if docs and not docs[-1].get("doc_id"):
                        docs[-1]["doc_id"] = doc_id
                        docs[-1]["collection"] = current_collection
                elif line and line[0].isdigit() and "**" in line:
                    # Title line like "1. **Document Title**"
                    title = line.split("**")[1] if "**" in line else line
                    docs.append({"title": title, "doc_id": "", "collection": current_collection})

    return docs


def parse_search_result(result: list, collection: str) -> tuple[list[dict], list[dict]]:
    """Parse search_collection result. Returns (chunks, sources)."""
    chunks = []
    sources = []

    if not result:
        return chunks, sources

    for item in result:
        if not isinstance(item, dict):
            continue

        if item.get("type") == "text":
            chunks.append({
                "text": item.get("text", ""),
                "collection": collection,
                "doc_id": "",
                "title": ""
            })
        elif item.get("type") == "resource":
            resource = item.get("resource", {})
            resource_text = resource.get("text", "")
            if resource_text:
                try:
                    payload = json.loads(resource_text)
                    if payload.get("fileCitations") and "sources" in payload:
                        sources.extend(payload["sources"])
                except json.JSONDecodeError:
                    pass

    return chunks, sources


def parse_read_result(result: list) -> tuple[str, list[dict]]:
    """Parse read_document result. Returns (content, sources)."""
    content = ""
    sources = []

    if not result:
        return content, sources

    for item in result:
        if not isinstance(item, dict):
            continue

        if item.get("type") == "text":
            content = item.get("text", "")
        elif item.get("type") == "resource":
            resource = item.get("resource", {})
            resource_text = resource.get("text", "")
            if resource_text:
                try:
                    payload = json.loads(resource_text)
                    if payload.get("fileCitations") and "sources" in payload:
                        sources.extend(payload["sources"])
                except json.JSONDecodeError:
                    pass

    return content, sources
```

#### 3. Add graph nodes
**File**: `scripts/pipes/neo_nl_agent.py` (append)

```python
# =============================================================================
# GRAPH NODES
# =============================================================================

async def route_query(state: GraphState) -> dict:
    """Classify query type and identify target collections."""
    await emit_status(state["_event_emitter"], "routing")

    prompt = ROUTER_PROMPT.format(query=state["query"])
    response = await call_llm(
        messages=[{"role": "user", "content": prompt}],
        request=state["_request"],
        user=state["_user"],
        model_id=state["_valves"].LLM_MODEL,
        stream=False
    )

    try:
        # Try to parse JSON from response
        # Handle cases where model wraps in markdown code blocks
        response_text = response.strip()
        if response_text.startswith("```"):
            response_text = response_text.split("```")[1]
            if response_text.startswith("json"):
                response_text = response_text[4:]

        result = json.loads(response_text.strip())
        return {
            "query_type": result.get("type", "factual"),
            "target_collections": result.get("collections", ["iaea"]),
        }
    except (json.JSONDecodeError, IndexError):
        log.warning(f"Failed to parse router response: {response[:100]}")
        return {
            "query_type": "factual",
            "target_collections": ["iaea"],
        }


async def discover_documents(state: GraphState) -> dict:
    """Find relevant documents across all collections."""
    await emit_status(state["_event_emitter"], "discovering")

    docs = await mcp_list_documents(
        state["_valves"].MCP_SERVER_URL,
        state["query"]
    )

    # Limit to top results
    docs = docs[:state["_valves"].MAX_DISCOVERY_RESULTS]

    return {"discovered_docs": docs}


async def read_document(state: GraphState) -> dict:
    """Read full content of the best matching document."""
    if not state.get("discovered_docs"):
        return {"document_content": None, "sources": []}

    await emit_status(state["_event_emitter"], "reading")

    # Use first discovered document
    doc = state["discovered_docs"][0]
    content, sources = await mcp_read_document(
        state["_valves"].MCP_SERVER_URL,
        state["query"],
        doc.get("doc_id", ""),
        doc.get("collection", "iaea")
    )

    return {"document_content": content, "sources": sources}


async def retrieve_chunks(state: GraphState) -> dict:
    """Search collections sequentially for relevant chunks."""
    await emit_status(state["_event_emitter"], "retrieving")

    all_chunks = []
    all_sources = []
    max_per_collection = state["_valves"].MAX_CHUNKS_PER_COLLECTION

    for collection in state["target_collections"]:
        chunks, sources = await mcp_search_collection(
            state["_valves"].MCP_SERVER_URL,
            state["query"],
            collection
        )
        all_chunks.extend(chunks[:max_per_collection])
        all_sources.extend(sources[:max_per_collection])

    return {"retrieved_chunks": all_chunks, "sources": all_sources}


async def retrieve_chunks_parallel(state: GraphState) -> dict:
    """Search multiple collections in parallel."""
    await emit_status(state["_event_emitter"], "retrieving")

    max_per_collection = state["_valves"].MAX_CHUNKS_PER_COLLECTION

    # Create tasks for parallel execution
    async def search_one(collection: str):
        return await mcp_search_collection(
            state["_valves"].MCP_SERVER_URL,
            state["query"],
            collection
        )

    tasks = [search_one(coll) for coll in state["target_collections"]]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_chunks = []
    all_sources = []

    for result in results:
        if isinstance(result, Exception):
            log.error(f"Parallel search error: {result}")
            continue
        chunks, sources = result
        all_chunks.extend(chunks[:max_per_collection])
        all_sources.extend(sources[:max_per_collection])

    return {"retrieved_chunks": all_chunks, "sources": all_sources}


async def summarize_content(state: GraphState) -> dict:
    """Compress retrieved content into key facts."""
    chunks = state.get("retrieved_chunks", [])
    doc_content = state.get("document_content")

    if not chunks and not doc_content:
        return {"summarized_facts": []}

    await emit_status(state["_event_emitter"], "summarizing")

    # Build chunks text for summarization
    if doc_content:
        chunks_text = f"[Full Document]\n{doc_content[:8000]}"
    else:
        chunks_text = "\n\n".join([
            f"[{c.get('collection', 'unknown')}:{c.get('doc_id', 'unknown')}] {c.get('title', '')}\n{c.get('text', '')}"
            for c in chunks[:20]
        ])

    prompt = SUMMARIZER_PROMPT.format(query=state["query"], chunks=chunks_text)
    response = await call_llm(
        messages=[{"role": "user", "content": prompt}],
        request=state["_request"],
        user=state["_user"],
        model_id=state["_valves"].LLM_MODEL,
        stream=False
    )

    try:
        # Parse JSON response
        response_text = response.strip()
        if response_text.startswith("```"):
            response_text = response_text.split("```")[1]
            if response_text.startswith("json"):
                response_text = response_text[4:]

        facts = json.loads(response_text.strip())
        return {"summarized_facts": facts if isinstance(facts, list) else []}
    except (json.JSONDecodeError, IndexError):
        log.warning(f"Failed to parse summarizer response: {response[:100]}")
        return {"summarized_facts": []}
```

### Success Criteria

#### Automated Verification:
- [x] Python syntax valid: `python -m py_compile scripts/pipes/neo_nl_agent.py`
- [x] All function signatures match expected format

#### Manual Verification:
- [ ] None for this phase

---

## Phase 3: Graph Assembly

### Overview
Define the routing logic, assemble the StateGraph with conditional edges, and create the graph compilation.

### Changes Required

#### 1. Add routing logic and graph builder
**File**: `scripts/pipes/neo_nl_agent.py` (append)

```python
# =============================================================================
# ROUTING LOGIC
# =============================================================================

def route_by_query_type(state: GraphState) -> str:
    """Determine next node based on query type."""
    query_type = state.get("query_type", "factual")

    if query_type == "factual":
        return "retrieve"
    elif query_type in ["exploratory", "deep_dive", "comparative"]:
        return "discover"
    else:
        return "retrieve"


def route_after_discovery(state: GraphState) -> str:
    """Determine next node after discovery."""
    query_type = state.get("query_type", "factual")

    if query_type == "deep_dive":
        return "read_document"
    elif query_type == "comparative":
        return "retrieve_parallel"
    else:
        return "retrieve"


# =============================================================================
# BUILD GRAPH
# =============================================================================

def build_rag_graph() -> StateGraph:
    """Build the LangGraph workflow."""
    graph = StateGraph(GraphState)

    # Add nodes
    graph.add_node("route", route_query)
    graph.add_node("discover", discover_documents)
    graph.add_node("read_document", read_document)
    graph.add_node("retrieve", retrieve_chunks)
    graph.add_node("retrieve_parallel", retrieve_chunks_parallel)
    graph.add_node("summarize", summarize_content)

    # Set entry point
    graph.set_entry_point("route")

    # Add conditional edges from route
    graph.add_conditional_edges(
        "route",
        route_by_query_type,
        {
            "discover": "discover",
            "retrieve": "retrieve",
        }
    )

    # Add conditional edges from discover
    graph.add_conditional_edges(
        "discover",
        route_after_discovery,
        {
            "read_document": "read_document",
            "retrieve": "retrieve",
            "retrieve_parallel": "retrieve_parallel",
        }
    )

    # Linear edges to summarize and end
    graph.add_edge("read_document", "summarize")
    graph.add_edge("retrieve", "summarize")
    graph.add_edge("retrieve_parallel", "summarize")
    graph.add_edge("summarize", END)

    return graph.compile()
```

### Success Criteria

#### Automated Verification:
- [x] Python syntax valid: `python -m py_compile scripts/pipes/neo_nl_agent.py`

#### Manual Verification:
- [ ] None for this phase

---

## Phase 4: Pipe Integration

### Overview
Create the Pipe class with Valves, implement source emission, and connect the graph to the pipe entry point.

### Changes Required

#### 1. Add source emission helper
**File**: `scripts/pipes/neo_nl_agent.py` (append)

```python
# =============================================================================
# SOURCE EMISSION
# =============================================================================

async def emit_sources(sources: list[dict], emitter, max_sources: int = 5):
    """Emit sources for Open WebUI citation display."""
    if not emitter or not sources:
        return

    for source in sources[:max_sources]:
        file_name = source.get("fileName", "Unknown Document")
        file_id = source.get("fileId", "")
        relevance = source.get("relevance", 0.75)
        metadata = source.get("metadata", {})
        url = metadata.get("url", "")
        chunk_content = source.get("chunk_content", "")

        source_data = {
            "source": {
                "id": file_id,
                "name": file_name,
                "url": url if url else None,
            },
            "document": [chunk_content] if chunk_content else [file_name],
            "metadata": [{"source": file_id, "name": file_name}],
            "distances": [1 - relevance] if relevance else [],
        }

        await emitter({"type": "source", "data": source_data})
```

#### 2. Add Pipe class
**File**: `scripts/pipes/neo_nl_agent.py` (append)

```python
# =============================================================================
# PIPE CLASS
# =============================================================================

class Pipe:
    class Valves(BaseModel):
        MCP_SERVER_URL: str = Field(
            default="http://host.docker.internal:3434/mcp",
            description="URL of the genai-utils MCP server"
        )
        LLM_MODEL: str = Field(
            default="",
            description="Model ID from Open WebUI (leave empty for auto-select)"
        )
        MAX_CHUNKS_PER_COLLECTION: int = Field(
            default=5,
            description="Maximum chunks to retrieve per collection"
        )
        MAX_DISCOVERY_RESULTS: int = Field(
            default=10,
            description="Maximum documents to return from discovery"
        )
        SKIP_RAG_TASKS: list = Field(
            default=["title_generation", "tags_generation", "query_generation",
                     "emoji_generation", "autocomplete_generation", "follow_up_generation"],
            description="System tasks that skip RAG"
        )

    def __init__(self):
        self.valves = self.Valves()
        self.graph = build_rag_graph()

    def _get_model_id(self, request) -> str:
        """Get model ID from valves or auto-select first non-pipe model."""
        if self.valves.LLM_MODEL:
            return self.valves.LLM_MODEL

        models = getattr(request.app.state, "MODELS", {})
        for mid, model in models.items():
            if not model.get("pipe"):
                return mid

        return "gpt-oss-openai"  # Fallback

    async def pipe(
        self,
        body: dict,
        __user__: dict = None,
        __task__: str = None,
        __request__=None,
        __event_emitter__=None,
    ) -> AsyncGenerator[str, None]:
        """Main pipe execution - runs the LangGraph workflow."""

        messages = body.get("messages", [])
        if not messages:
            yield "No messages provided."
            return

        user_message = messages[-1].get("content", "")

        # Skip RAG for system tasks
        if __task__ in self.valves.SKIP_RAG_TASKS:
            log.info(f"[NEO NL Agent] System task '{__task__}', skipping RAG")
            model_id = self._get_model_id(__request__)
            stream = await call_llm(
                messages=[{"role": "user", "content": user_message}],
                request=__request__,
                user=__user__,
                model_id=model_id,
                stream=True
            )
            async for chunk in stream:
                yield chunk
            return

        log.info(f"[NEO NL Agent] Processing: {user_message[:100]}...")

        # Ensure we have a model ID
        model_id = self._get_model_id(__request__)

        # Create a modified valves with the resolved model ID
        valves = self.Valves(**self.valves.model_dump())
        valves.LLM_MODEL = model_id

        # Initialize graph state
        initial_state: GraphState = {
            "query": user_message,
            "messages": messages,
            "query_type": "factual",
            "target_collections": [],
            "discovered_docs": [],
            "retrieved_chunks": [],
            "sources": [],
            "document_content": None,
            "summarized_facts": [],
            "_request": __request__,
            "_user": __user__,
            "_event_emitter": __event_emitter__,
            "_valves": valves,
        }

        try:
            # Run the graph (non-streaming retrieval and summarization)
            final_state = await self.graph.ainvoke(initial_state)

            # Emit sources
            await emit_sources(
                final_state.get("sources", []),
                __event_emitter__,
                self.valves.MAX_CHUNKS_PER_COLLECTION
            )

            # Build context from summarized facts
            facts = final_state.get("summarized_facts", [])
            if facts:
                context = "\n".join([
                    f"[{i+1}] {f.get('fact', '')} (Bron: {f.get('title', f.get('source', 'onbekend'))})"
                    for i, f in enumerate(facts)
                ])
            else:
                context = "Geen relevante informatie gevonden in de documenten."

            # Build final messages
            system_prompt = SYSTEM_PROMPT.format(context=context)
            final_messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ]

            # Stream the final response
            await emit_status(__event_emitter__, "synthesizing")
            stream = await call_llm(
                messages=final_messages,
                request=__request__,
                user=__user__,
                model_id=model_id,
                stream=True
            )
            async for chunk in stream:
                yield chunk

            # Done
            await emit_status(__event_emitter__, "done", done=True)

        except Exception as e:
            log.error(f"[NEO NL Agent] Graph execution failed: {e}", exc_info=True)
            yield f"Error: {str(e)}"
```

### Success Criteria

#### Automated Verification:
- [x] Python syntax valid: `python -m py_compile scripts/pipes/neo_nl_agent.py`
- [x] Full file structure complete with all sections

#### Manual Verification:
- [ ] Deploy via `bootstrap_functions.py` and verify it appears in Open WebUI
- [ ] Test with a factual query: "Wat is het IAEA standaard voor nucleaire veiligheid?"
- [ ] Test with an exploratory query: "Welke documenten gaan over nucleaire veiligheid?"
- [ ] Test with a comparative query: "Vergelijk IAEA en ANVS regelgeving over straling"
- [ ] Verify sources appear correctly in the citation display
- [ ] Verify fun status messages appear during processing

---

## Testing Strategy

### Unit Tests
The pipe can be tested by importing and calling functions directly:
- Test `parse_discovery_result()` with sample MCP responses
- Test `parse_search_result()` with sample MCP responses
- Test `route_by_query_type()` with different state values

### Integration Tests
- Deploy to Open WebUI dev instance
- Run queries of each type and verify routing
- Check logs for expected flow through nodes

### Manual Testing Steps
1. Deploy pipe via `python scripts/pipes/bootstrap_functions.py --env-file ../../.env.neo`
2. Open Open WebUI and select "NEO NL Agent" model
3. Test factual query: "Wat zijn de IAEA safety standards?"
4. Verify: Status shows "Searching documents...", sources appear, response cites [1], [2], etc.
5. Test exploratory query: "Welke documenten zijn er over SMR?"
6. Verify: Status shows "Discovering relevant documents...", then retrieval
7. Test comparative query: "Vergelijk IAEA en ANVS standaarden"
8. Verify: Multiple collections searched (check logs for parallel retrieval)

## Performance Considerations

- **Latency**: Multi-step workflow adds latency vs. single-step RAG. Expected 5-10s vs. 3-5s for neo_nl_assistant.
- **Token usage**: Additional LLM calls for routing and summarization. ~500-1000 extra tokens per query.
- **MCP connections**: Each node creates new MCPClient. Consider connection pooling if this becomes an issue.

## References

- Research document: `thoughts/shared/research/2026-01-04-multi-agent-rag-architecture.md`
- Existing pipe: `scripts/pipes/neo_nl_assistant.py`
- MCP client: `backend/open_webui/utils/mcp/client.py`
- Pipe execution: `backend/open_webui/functions.py:158-353`
- Event emitter: `backend/open_webui/socket/main.py:693-810`
