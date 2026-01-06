---
date: 2026-01-04
author: Claude
status: draft
repository: open-webui
branch: main
---

# NEO NL Multi-Agent Topic Decomposition Pipe Implementation Plan

## Overview

Create a new pipe `neo_nl_multiagent.py` that implements multi-agent topic decomposition for complex comparative queries. When a user asks a question that spans multiple domains (IAEA, ANVS, Dutch law, security), the pipe spawns parallel sub-agents to research each collection independently, then synthesizes the results into a coherent answer.

## Current State Analysis

### Existing Implementation (`neo_nl_agent.py`)
- Single-pass DAG with conditional routing based on query type
- Query types: `factual`, `exploratory`, `deep_dive`, `comparative`
- For `comparative` queries: uses `retrieve_chunks_parallel()` which searches the **same query** across multiple collections
- No topic decomposition: all collections receive identical search queries
- Sequential summarization of combined results

### What's Missing
- No **per-collection query refinement** (each collection gets the same query)
- No **parallel summarization** (one summarization pass for all content)
- No **synthesis step** to merge collection-specific insights

### Available MCP Collections
| Collection | Domain | Use For |
|------------|--------|---------|
| `iaea` | International standards | International norms, best practices, terminology |
| `anvs` | Dutch regulator | Dutch oversight, licensing, policy, guidance |
| `wetten_overheid` | Dutch law | Legal obligations, formal definitions, authority |
| `security` | Security | Nuclear security, access control, safeguards |

## Desired End State

A new pipe that:
1. Detects comparative/multi-domain queries
2. Decomposes them into collection-specific research tasks
3. Executes all research tasks in parallel
4. Synthesizes findings into a unified answer with proper attribution

### Verification
- Pipe appears in Open WebUI model selector
- Comparative queries (e.g., "Hoe verschillen IAEA en ANVS eisen voor veiligheidscultuur?") trigger parallel research
- Response includes insights from multiple collections with proper source attribution
- Latency is comparable to or faster than sequential approach despite more LLM calls

## What We're NOT Doing

- No frontend changes for parallel progress display
- No recursive self-reflection (that's a separate enhancement)
- No dynamic topic decomposition via LLM (using predefined collection-based splits)
- No changes to existing `neo_nl_agent.py`

## Implementation Approach

Use **predefined collection-based decomposition** rather than LLM-driven topic splitting:
- Simpler to implement and debug
- Deterministic behavior
- Leverages existing collection structure
- Can be enhanced later with LLM-driven decomposition

## Phase 1: Core Multi-Agent Pipe Structure

### Overview
Create the new pipe file with extended state, decomposition logic, and parallel research execution.

### Changes Required

#### 1. Create New Pipe File
**File**: `scripts/pipes/neo_nl_multiagent.py`

```python
"""
title: NEO NL Multi-Agent
description: Multi-agent RAG with parallel topic decomposition for comparative queries
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
```

#### 2. Extended GraphState
**File**: `scripts/pipes/neo_nl_multiagent.py`

```python
class GraphState(TypedDict):
    """State that flows through the LangGraph workflow"""
    # Input
    query: str
    messages: list[dict]

    # Routing
    query_type: Literal["factual", "exploratory", "deep_dive", "comparative"]
    target_collections: list[str]

    # NEW: Topic decomposition
    sub_topics: list[dict]  # [{"collection": str, "query": str, "focus": str}]
    sub_results: list[dict]  # Results from each sub-agent

    # Retrieved content (for non-comparative paths)
    discovered_docs: list[dict]
    retrieved_chunks: list[dict]
    sources: list[dict]
    document_content: Optional[str]

    # Summarized
    summarized_facts: list[dict]

    # Context for Open WebUI
    _request: Any
    _user: dict
    _event_emitter: Any
    _valves: Any
```

#### 3. Collection Focus Definitions
**File**: `scripts/pipes/neo_nl_multiagent.py`

```python
# Define what each collection is best for
COLLECTION_FOCUS = {
    "iaea": {
        "name": "IAEA (International)",
        "focus": "internationale normen, veiligheidsprincipes, beste praktijken",
        "query_prefix": "Wat zegt de IAEA over",
    },
    "anvs": {
        "name": "ANVS (Nederlands toezicht)",
        "focus": "Nederlandse interpretatie, vergunningverlening, beleid",
        "query_prefix": "Wat is het ANVS standpunt over",
    },
    "wetten_overheid": {
        "name": "Nederlandse wetgeving",
        "focus": "wettelijke verplichtingen, juridische kaders, definities",
        "query_prefix": "Wat zegt de Nederlandse wet over",
    },
    "security": {
        "name": "Beveiliging",
        "focus": "nucleaire beveiliging, fysieke bescherming, safeguards",
        "query_prefix": "Wat zijn de beveiligingseisen voor",
    },
}
```

#### 4. New Prompts
**File**: `scripts/pipes/neo_nl_multiagent.py`

```python
TOPIC_QUERY_PROMPT = """Reformuleer de gebruikersvraag voor specifiek zoeken in {collection_name}.

Originele vraag: {query}

Focus van deze collectie: {focus}

Maak een zoekquery die:
1. Specifiek is voor deze collectie
2. De kernvraag behoudt
3. Relevante termen voor deze bron gebruikt

Geef alleen de zoekquery, geen uitleg."""

COLLECTION_SUMMARIZER_PROMPT = """Vat de belangrijkste feiten samen uit {collection_name} over: {query}

Documenten:
{chunks}

Focus op: {focus}

Regels:
1. Alleen feiten relevant voor de vraag
2. Elke feit moet een citatie hebben
3. Maximum 3 feiten per collectie
4. Als geen relevante feiten, return lege lijst

Output JSON: [{{"fact": "...", "source": "{collection}:doc_id", "title": "..."}}]"""

SYNTHESIZER_PROMPT = """Synthetiseer de onderzoeksresultaten van meerdere bronnen.

Originele vraag: {query}

Onderzoeksresultaten per bron:
{research_results}

Instructies:
1. Combineer inzichten van alle bronnen
2. Identificeer overeenkomsten en verschillen
3. Geef een gebalanceerd overzicht
4. Behoud bronverwijzingen

Output JSON array van feiten:
[{{"fact": "...", "source": "collection:doc_id", "title": "...", "comparison_note": "optioneel"}}]"""
```

#### 5. New Status Messages
**File**: `scripts/pipes/neo_nl_multiagent.py`

```python
STATUS_MESSAGES = {
    "routing": [
        "Analyzing your question...",
        "Decoding your nuclear inquiry...",
    ],
    "decomposing": [
        "Breaking down your question...",
        "Identifying research angles...",
    ],
    "researching_parallel": [
        "Researching multiple sources in parallel...",
        "Consulting IAEA, ANVS, wetgeving en beveiliging...",
    ],
    "summarizing_collection": [
        "Analyzing {collection} findings...",
        "Extracting key facts from {collection}...",
    ],
    "synthesizing": [
        "Combining research results...",
        "Synthesizing multi-source insights...",
    ],
    "generating": [
        "Generating response...",
        "Formulating your answer...",
    ],
    "done": [
        "Complete",
        "Research complete",
    ],
}

async def emit_status(emitter, phase: str, done: bool = False, **kwargs):
    """Emit status message to UI."""
    if not emitter:
        return
    messages = STATUS_MESSAGES.get(phase, ["Processing..."])
    message = random.choice(messages)
    # Format with any provided kwargs
    message = message.format(**kwargs) if kwargs else message
    await emitter({"type": "status", "data": {"description": message, "done": done}})
```

### Success Criteria

#### Automated Verification:
- [x] File exists: `ls scripts/pipes/neo_nl_multiagent.py`
- [x] Python syntax valid: `python -m py_compile scripts/pipes/neo_nl_multiagent.py`
- [x] Imports work: `python -c "from scripts.pipes.neo_nl_multiagent import Pipe"`

#### Manual Verification:
- [ ] File structure matches plan

---

## Phase 2: Decomposition and Parallel Research Nodes

### Overview
Implement the core nodes for query decomposition and parallel sub-agent research.

### Changes Required

#### 1. Decompose Query Node
**File**: `scripts/pipes/neo_nl_multiagent.py`

```python
async def decompose_query(state: GraphState) -> dict:
    """Decompose a comparative query into collection-specific research tasks."""
    await emit_status(state["_event_emitter"], "decomposing")

    target_collections = state.get("target_collections", [])
    if not target_collections:
        target_collections = list(COLLECTION_FOCUS.keys())

    sub_topics = []

    for collection in target_collections:
        config = COLLECTION_FOCUS.get(collection, {})

        # Generate collection-specific query
        prompt = TOPIC_QUERY_PROMPT.format(
            collection_name=config.get("name", collection),
            query=state["query"],
            focus=config.get("focus", "")
        )

        refined_query = await call_llm(
            messages=[{"role": "user", "content": prompt}],
            request=state["_request"],
            user=state["_user"],
            model_id=state["_valves"].LLM_MODEL,
            stream=False
        )

        sub_topics.append({
            "collection": collection,
            "query": refined_query.strip(),
            "focus": config.get("focus", ""),
            "name": config.get("name", collection),
        })

    log.info(f"[MultiAgent] Decomposed into {len(sub_topics)} sub-topics")
    return {"sub_topics": sub_topics}
```

#### 2. Research Sub-Topics Node (Parallel Execution)
**File**: `scripts/pipes/neo_nl_multiagent.py`

```python
async def research_sub_topics(state: GraphState) -> dict:
    """Research all sub-topics in parallel with per-collection summarization."""
    await emit_status(state["_event_emitter"], "researching_parallel")

    mcp_url = state["_valves"].MCP_SERVER_URL
    max_chunks = state["_valves"].MAX_CHUNKS_PER_COLLECTION

    async def research_one(topic: dict) -> dict:
        """Research a single collection and summarize findings."""
        collection = topic["collection"]
        query = topic["query"]

        # Retrieve chunks from this collection
        chunks, sources = await mcp_search_collection(mcp_url, query, collection)
        chunks = chunks[:max_chunks]
        sources = sources[:max_chunks]

        if not chunks:
            return {
                "collection": collection,
                "name": topic.get("name", collection),
                "facts": [],
                "sources": [],
            }

        # Summarize findings for this collection
        chunks_text = "\n\n".join([
            f"[{c.get('doc_id', 'unknown')}] {c.get('title', '')}\n{c.get('text', '')}"
            for c in chunks
        ])

        prompt = COLLECTION_SUMMARIZER_PROMPT.format(
            collection_name=topic.get("name", collection),
            query=topic["query"],
            chunks=chunks_text,
            focus=topic.get("focus", ""),
            collection=collection,
        )

        response = await call_llm(
            messages=[{"role": "user", "content": prompt}],
            request=state["_request"],
            user=state["_user"],
            model_id=state["_valves"].LLM_MODEL,
            stream=False
        )

        try:
            response_text = response.strip()
            if response_text.startswith("```"):
                response_text = response_text.split("```")[1]
                if response_text.startswith("json"):
                    response_text = response_text[4:]
            facts = json.loads(response_text.strip())
        except (json.JSONDecodeError, IndexError):
            log.warning(f"Failed to parse {collection} summary: {response[:100]}")
            facts = []

        return {
            "collection": collection,
            "name": topic.get("name", collection),
            "facts": facts if isinstance(facts, list) else [],
            "sources": sources,
        }

    # Execute all research tasks in parallel
    tasks = [research_one(topic) for topic in state.get("sub_topics", [])]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Filter out exceptions
    valid_results = []
    for result in results:
        if isinstance(result, Exception):
            log.error(f"Sub-agent research error: {result}")
            continue
        valid_results.append(result)

    log.info(f"[MultiAgent] Completed {len(valid_results)}/{len(tasks)} sub-agent research tasks")
    return {"sub_results": valid_results}
```

#### 3. Synthesize Results Node
**File**: `scripts/pipes/neo_nl_multiagent.py`

```python
async def synthesize_results(state: GraphState) -> dict:
    """Synthesize sub-topic results into unified facts."""
    await emit_status(state["_event_emitter"], "synthesizing")

    sub_results = state.get("sub_results", [])

    if not sub_results:
        return {"summarized_facts": [], "sources": []}

    # Collect all sources
    all_sources = []
    for result in sub_results:
        all_sources.extend(result.get("sources", []))

    # Check if we have any facts to synthesize
    total_facts = sum(len(r.get("facts", [])) for r in sub_results)
    if total_facts == 0:
        return {"summarized_facts": [], "sources": all_sources}

    # Build research results text for synthesis
    research_text = ""
    for result in sub_results:
        collection_name = result.get("name", result.get("collection", "Unknown"))
        facts = result.get("facts", [])
        if facts:
            research_text += f"\n## {collection_name}\n"
            for fact in facts:
                research_text += f"- {fact.get('fact', '')} (Bron: {fact.get('title', fact.get('source', 'onbekend'))})\n"

    prompt = SYNTHESIZER_PROMPT.format(
        query=state["query"],
        research_results=research_text
    )

    response = await call_llm(
        messages=[{"role": "user", "content": prompt}],
        request=state["_request"],
        user=state["_user"],
        model_id=state["_valves"].LLM_MODEL,
        stream=False
    )

    try:
        response_text = response.strip()
        if response_text.startswith("```"):
            response_text = response_text.split("```")[1]
            if response_text.startswith("json"):
                response_text = response_text[4:]
        synthesized_facts = json.loads(response_text.strip())
    except (json.JSONDecodeError, IndexError):
        log.warning(f"Failed to parse synthesis: {response[:100]}")
        # Fallback: flatten all collection facts
        synthesized_facts = []
        for result in sub_results:
            synthesized_facts.extend(result.get("facts", []))

    return {
        "summarized_facts": synthesized_facts if isinstance(synthesized_facts, list) else [],
        "sources": all_sources,
    }
```

### Success Criteria

#### Automated Verification:
- [x] Python syntax valid: `python -m py_compile scripts/pipes/neo_nl_multiagent.py`
- [x] All functions defined: `python -c "from scripts.pipes.neo_nl_multiagent import decompose_query, research_sub_topics, synthesize_results"`

#### Manual Verification:
- [ ] Node logic follows the research document architecture

---

## Phase 3: Graph Assembly and Routing

### Overview
Build the LangGraph workflow with the multi-agent path for comparative queries.

### Changes Required

#### 1. Routing Logic
**File**: `scripts/pipes/neo_nl_multiagent.py`

```python
def route_by_query_type(state: GraphState) -> str:
    """Determine next node based on query type."""
    query_type = state.get("query_type", "factual")

    # Comparative queries go through multi-agent decomposition
    if query_type == "comparative":
        return "decompose"
    elif query_type == "factual":
        return "retrieve"
    elif query_type in ["exploratory", "deep_dive"]:
        return "discover"
    else:
        return "retrieve"


def route_after_discovery(state: GraphState) -> str:
    """Determine next node after discovery."""
    query_type = state.get("query_type", "factual")

    if query_type == "deep_dive":
        return "read_document"
    else:
        return "retrieve"
```

#### 2. Build Graph Function
**File**: `scripts/pipes/neo_nl_multiagent.py`

```python
def build_multiagent_rag_graph() -> StateGraph:
    """Build the LangGraph workflow with multi-agent path."""
    graph = StateGraph(GraphState)

    # Add all nodes
    graph.add_node("route", route_query)
    graph.add_node("discover", discover_documents)
    graph.add_node("read_document", read_document)
    graph.add_node("retrieve", retrieve_chunks)
    graph.add_node("summarize", summarize_content)

    # Multi-agent nodes
    graph.add_node("decompose", decompose_query)
    graph.add_node("research_parallel", research_sub_topics)
    graph.add_node("synthesize", synthesize_results)

    # Entry point
    graph.set_entry_point("route")

    # Route based on query type
    graph.add_conditional_edges(
        "route",
        route_by_query_type,
        {
            "decompose": "decompose",      # comparative -> multi-agent
            "discover": "discover",         # exploratory/deep_dive
            "retrieve": "retrieve",         # factual
        }
    )

    # Multi-agent path: decompose -> research_parallel -> synthesize -> END
    graph.add_edge("decompose", "research_parallel")
    graph.add_edge("research_parallel", "synthesize")
    graph.add_edge("synthesize", END)

    # Discovery path
    graph.add_conditional_edges(
        "discover",
        route_after_discovery,
        {
            "read_document": "read_document",
            "retrieve": "retrieve",
        }
    )

    # Standard paths to summarize and end
    graph.add_edge("read_document", "summarize")
    graph.add_edge("retrieve", "summarize")
    graph.add_edge("summarize", END)

    return graph.compile()
```

#### 3. Graph Visualization (for reference)
```
                    route
                      │
         ┌────────────┼────────────────────┐
         │            │                    │
         ▼            ▼                    ▼
     decompose    discover             retrieve
         │            │                    │
         ▼       ┌────┴────┐               │
   research      │         │               │
   _parallel   read_doc  retrieve          │
         │        │         │              │
         ▼        └────┬────┘              │
    synthesize         │                   │
         │             ▼                   │
         │         summarize               │
         │             │                   │
         └─────────────┴───────────────────┘
                       │
                       ▼
                      END
```

### Success Criteria

#### Automated Verification:
- [x] Graph compiles: `python -c "from scripts.pipes.neo_nl_multiagent import build_multiagent_rag_graph; g = build_multiagent_rag_graph(); print('Graph compiled')"`
- [x] All edges valid (no missing nodes)

#### Manual Verification:
- [ ] Graph structure matches visualization

---

## Phase 4: Pipe Class and Integration

### Overview
Complete the Pipe class with configuration and execution logic.

### Changes Required

#### 1. Pipe Class
**File**: `scripts/pipes/neo_nl_multiagent.py`

```python
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
        self.graph = build_multiagent_rag_graph()

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
        """Main pipe execution - runs the multi-agent LangGraph workflow."""

        messages = body.get("messages", [])
        if not messages:
            yield "No messages provided."
            return

        user_message = messages[-1].get("content", "")

        # Skip RAG for system tasks
        if __task__ in self.valves.SKIP_RAG_TASKS:
            log.info(f"[NEO NL MultiAgent] System task '{__task__}', skipping RAG")
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

        log.info(f"[NEO NL MultiAgent] Processing: {user_message[:100]}...")

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
            "sub_topics": [],
            "sub_results": [],
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
            # Run the graph
            final_state = await self.graph.ainvoke(initial_state)

            # Emit sources
            await emit_sources(
                final_state.get("sources", []),
                __event_emitter__,
                self.valves.MAX_CHUNKS_PER_COLLECTION * 4  # Up to 4 collections
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
            await emit_status(__event_emitter__, "generating")
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
            log.error(f"[NEO NL MultiAgent] Graph execution failed: {e}", exc_info=True)
            yield f"Error: {str(e)}"
```

#### 2. Copy Helper Functions from Original
Copy these functions from `neo_nl_agent.py`:
- `call_llm()` (lines 112-158)
- `mcp_list_documents()` (lines 234-248)
- `mcp_search_collection()` (lines 251-268)
- `mcp_read_document()` (lines 271-289)
- `parse_discovery_result()` (lines 292-320)
- `parse_search_result()` (lines 323-353)
- `parse_read_result()` (lines 356-380)
- `route_query()` (lines 388-426)
- `discover_documents()` (lines 429-441)
- `read_document()` (lines 444-460)
- `retrieve_chunks()` (lines 463-480)
- `summarize_content()` (lines 514-554)
- `emit_sources()` (lines 638-662)
- `ROUTER_PROMPT` (lines 165-202)
- `SUMMARIZER_PROMPT` (lines 204-215)
- `SYSTEM_PROMPT` (lines 217-227)

### Success Criteria

#### Automated Verification:
- [x] Python syntax valid: `python -m py_compile scripts/pipes/neo_nl_multiagent.py`
- [x] Pipe instantiates: `python -c "from scripts.pipes.neo_nl_multiagent import Pipe; p = Pipe(); print('Pipe created')"`
- [x] Graph is accessible: `python -c "from scripts.pipes.neo_nl_multiagent import Pipe; p = Pipe(); print(type(p.graph))"`

#### Manual Verification:
- [ ] Pipe appears in Open WebUI model selector when placed in appropriate directory

**Implementation Note**: After completing this phase, deploy the pipe to Open WebUI and verify it appears in the model selector before proceeding to testing.

---

## Phase 5: Testing and Validation

### Overview
Test the multi-agent pipe with various query types.

### Test Cases

#### Test 1: Comparative Query (Multi-Agent Path)
**Query**: "Hoe verschillen IAEA en ANVS eisen voor veiligheidscultuur?"

**Expected behavior**:
1. Router classifies as `comparative`
2. Decompose creates 4 sub-topics (one per collection)
3. Parallel research retrieves from all collections
4. Synthesis combines findings with comparison notes
5. Response includes insights from multiple sources

#### Test 2: Factual Query (Standard Path)
**Query**: "Wat is IAEA GSR Part 2?"

**Expected behavior**:
1. Router classifies as `factual`
2. Goes through standard retrieve → summarize path
3. No decomposition occurs

#### Test 3: Deep Dive Query (Discovery Path)
**Query**: "Geef een gedetailleerde uitleg van de Kernenergiewet"

**Expected behavior**:
1. Router classifies as `deep_dive`
2. Goes through discover → read_document → summarize path
3. No decomposition occurs

#### Test 4: Error Handling
**Query**: Trigger MCP connection failure

**Expected behavior**:
1. Sub-agent errors are logged
2. Other sub-agents continue
3. Partial results are synthesized
4. User sees meaningful response

### Success Criteria

#### Automated Verification:
- [ ] No Python errors during test queries
- [ ] Log shows correct path taken for each query type

#### Manual Verification:
- [ ] Comparative queries trigger parallel research (check logs)
- [ ] Response quality is good for comparative queries
- [ ] Latency is acceptable (< 30 seconds for comparative queries)
- [ ] Source attribution is correct

---

## Testing Strategy

### Unit Tests (Future)
- Mock MCP client responses
- Test decomposition logic
- Test synthesis logic
- Test error handling in parallel execution

### Integration Tests (Manual)
1. Deploy pipe to Open WebUI
2. Test each query type
3. Verify logs show correct graph path
4. Check response quality and source attribution

### Performance Testing
- Measure latency: single-pass vs multi-agent
- Expected: Multi-agent may be slower due to more LLM calls, but provides richer answers

## Performance Considerations

### LLM Call Count
| Query Type | Single-Pass Pipe | Multi-Agent Pipe |
|------------|------------------|------------------|
| Factual | 3 calls | 3 calls (same path) |
| Comparative | 3 calls | 2 + 4 + 1 + 1 = 8 calls |

- 1 route call
- 4 query refinement calls (parallel, ~1s each)
- 4 retrieval + summarization calls (parallel, ~2-3s each)
- 1 synthesis call
- 1 final generation call

### Latency Optimization
- Parallel execution of sub-agent research
- Could add caching for repeated queries (future enhancement)
- Could reduce LLM calls by skipping query refinement (use original query for all collections)

## Migration Notes

N/A - This is a new pipe, no migration needed.

## References

- Research document: `thoughts/shared/research/2026-01-04-neo-nl-agent-recursive-multiagent.md`
- Original pipe: `scripts/pipes/neo_nl_agent.py`
- Open WebUI event emitter: `backend/open_webui/socket/main.py:693-810`
- LangGraph documentation: https://langchain-ai.github.io/langgraph/
