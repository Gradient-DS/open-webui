---
date: 2026-01-04T14:30:00+01:00
researcher: Claude
git_commit: 7d753a1ac075c8b029e349f61a5f07e4800317ba
branch: main
repository: open-webui
topic: "NEO NL Pipe LibreChat Feature Parity Analysis"
tags: [research, neo-nl, mcp, pipe, librechat, migration]
status: complete
last_updated: 2026-01-04
last_updated_by: Claude
last_updated_note: "Added Option D: LangChain/LangGraph agentic approach"
---

# Research: NEO NL Pipe LibreChat Feature Parity Analysis

**Date**: 2026-01-04T14:30:00+01:00
**Researcher**: Claude
**Git Commit**: 7d753a1ac075c8b029e349f61a5f07e4800317ba
**Branch**: main
**Repository**: open-webui

## Research Question

Can the current `scripts/pipes/neo_nl_assistant.py` achieve the same functionality as the LibreChat deployment, which had:
1. A comprehensive system prompt with NEO NL context and workflow instructions
2. Three MCP tools (list_documents, search_collection, and potentially read_document)
3. Reasoning set to "high" for the gpt-oss-120b model

## Summary

**The current pipe can achieve ~70% of the LibreChat functionality, but there are critical gaps:**

| Feature | LibreChat | Current Pipe | Gap |
|---------|-----------|--------------|-----|
| System Prompt | Comprehensive NEO NL context | Minimal citation guidance | **Missing** |
| list_documents tool | Available for document discovery | Not used | **Missing** |
| search_collection tool | Used via agent decision | Hardcoded single collection | **Partial** |
| Multi-collection routing | LLM decides which collection | Keyword-based detection | **Partial** |
| Reasoning effort | "high" | Not configured | **Missing** |
| Agentic workflow | LLM calls tools as needed | Fixed RAG pipeline | **Architectural difference** |

**Key insight**: The LibreChat deployment used an **agentic workflow** where the LLM autonomously decided when to call tools. The current pipe implements a **fixed RAG pipeline** that cannot adapt its search strategy based on the query.

## Detailed Findings

### 1. Current Pipe Implementation (`scripts/pipes/neo_nl_assistant.py`)

The pipe currently:
- Connects to genai-utils MCP server at `http://host.docker.internal:3434/mcp`
- Only calls `search_collection` with a single hardcoded collection ("iaea" by default)
- Has a minimal system prompt focused only on citation format
- Uses Open WebUI's internal model routing via `generate_chat_completion()`
- Emits source events for citation display
- Skips RAG for system tasks (title generation, follow-ups, etc.)

**Code reference**: `scripts/pipes/neo_nl_assistant.py:64-85` (MCP search), `scripts/pipes/neo_nl_assistant.py:21-34` (system prompt)

### 2. genai-utils MCP Server Tools

The MCP server exposes two active tools:

**`list_documents(query)`** - `/Users/lexlubbers/Code/soev/genai-utils/api/mcp_server.py:668-801`
- Searches ALL collections in parallel for document discovery
- Returns document metadata (title, doc_id, year, collection) without content
- Uses keyword search on titles only
- Returns max 5 results per collection
- **Currently NOT used by the pipe**

**`search_collection(query, collection)`** - `/Users/lexlubbers/Code/soev/genai-utils/api/mcp_server.py:844-921`
- Searches a single collection for text chunks with citations
- Returns `TextContent` with chunk text + `EmbeddedResource` with source metadata
- Returns 10 chunks maximum
- **Currently used by the pipe**

**Available collections**: `anvs`, `iaea`, `wetten_overheid`, `security`

### 3. Reasoning Effort Configuration

Open WebUI supports `reasoning_effort` for OpenAI o-series models:
- `/Users/lexlubbers/Code/soev/open-webui/backend/open_webui/utils/payload.py:114`
- Passed directly in the API payload as a string ("low", "medium", "high")

**To add to the pipe**, modify the payload in `_call_llm()`:

```python
payload = {
    "model": model_id,
    "messages": messages,
    "stream": True,
    "reasoning_effort": "high",  # Add this for o-series models
}
```

Or add to Valves for configurability:

```python
REASONING_EFFORT: str = Field(
    default="high",
    description="Reasoning effort for OpenAI o-series models (low, medium, high)"
)
```

### 4. LibreChat vs Pipe Architecture

**LibreChat (Agentic Workflow)**:
```
User Query → LLM decides tools → list_documents → LLM evaluates → search_collection(s) → LLM synthesizes → Response
```
- LLM autonomously chooses which collections to search
- Can call list_documents first to discover relevant documents
- Can search multiple collections based on findings
- System prompt guides the workflow

**Current Pipe (Fixed RAG Pipeline)**:
```
User Query → search_collection(default) → Inject context → LLM generates → Response
```
- Fixed single-collection search
- No discovery phase
- No adaptive routing

### 5. System Prompt Gap

**LibreChat system prompt included**:
- NEO NL organizational context (staatsdeelneming, kerncentrales, SMR-strategie)
- Dutch nuclear energy context (Borssele, new plants, climate goals)
- Collection descriptions and use cases (IAEA, ANVS, wetten_overheid, security)
- Workflow instructions (list_documents → search → evaluate → respond)
- Response formatting guidelines (< 400 words, Dutch, citations)
- Expertise areas and limitations

**Current pipe system prompt**:
- Only citation formatting guidance
- Basic instructions to answer in Dutch
- Missing all domain context

## Options to Achieve Parity

### Option A: Enhanced Pipe with Multi-Collection Search (Recommended)

Modify the pipe to search all relevant collections proactively:

```python
async def pipe(self, body, __user__, __request__, __event_emitter__):
    user_message = messages[-1]["content"]

    # Step 1: Discover documents across all collections
    mcp_client = MCPClient()
    await mcp_client.connect(self.valves.MCP_SERVER_URL)

    discovery_results = await mcp_client.call_tool(
        "list_documents", {"query": user_message}
    )

    # Step 2: Identify relevant collections from discovery
    relevant_collections = self._extract_collections(discovery_results)

    # Step 3: Search each relevant collection
    all_chunks = []
    for collection in relevant_collections:
        results = await mcp_client.call_tool(
            "search_collection",
            {"query": user_message, "collection": collection}
        )
        all_chunks.extend(self._parse_results(results, collection))

    await mcp_client.disconnect()

    # Step 4: Use comprehensive system prompt with all context
    system_prompt = FULL_NEO_NL_SYSTEM_PROMPT.format(context=all_chunks)

    # Step 5: Generate with reasoning effort
    payload = {
        "model": model_id,
        "messages": [{"role": "system", "content": system_prompt}, ...],
        "reasoning_effort": "high"
    }
```

**Pros**: Keeps pipe architecture, adds discovery and multi-collection search
**Cons**: Fixed two-step workflow, not truly agentic

### Option B: Use Open WebUI's Native MCP Integration

Instead of a custom pipe, configure the MCP server as a Tool Server in Open WebUI:

1. Admin → Settings → Tools → Add MCP Server
2. URL: `http://genai-utils:3434/mcp`
3. Create a Model with the MCP tools enabled
4. Add the full system prompt to the Model's system message
5. Configure `reasoning_effort: high` in model params

**Pros**: True agentic behavior, LLM decides tool calls, simpler maintenance
**Cons**: Requires MCP server to be accessible from Open WebUI, different user experience

### Option C: Hybrid Approach

Use a pipe for custom preprocessing + Open WebUI's MCP integration:

1. Filter pipe for system tasks (skip RAG for title generation)
2. MCP tools exposed as native tools for regular queries
3. Full system prompt in model configuration
4. Pipe handles source event formatting

### Option D: LangChain/LangGraph Agentic Pipe (Full Parity)

Use LangChain or LangGraph within the pipe to create a true agentic system where the LLM autonomously decides tool calls:

```python
"""
title: NEO NL Agent
requirements: langchain, langgraph, langchain-openai
"""

from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent
from open_webui.utils.mcp.client import MCPClient

# Define MCP tools as LangChain tools
@tool
async def list_documents(query: str) -> str:
    """Discover documents across all NEO NL collections (IAEA, ANVS, wetten_overheid, security)."""
    client = MCPClient()
    await client.connect(MCP_URL)
    result = await client.call_tool("list_documents", {"query": query})
    await client.disconnect()
    return format_result(result)

@tool
async def search_collection(query: str, collection: str) -> str:
    """Search a specific collection for detailed document chunks with citations.

    Args:
        query: Search query
        collection: One of 'anvs', 'iaea', 'wetten_overheid', 'security'
    """
    client = MCPClient()
    await client.connect(MCP_URL)
    result = await client.call_tool("search_collection", {"query": query, "collection": collection})
    await client.disconnect()
    return format_result(result)


class Pipe:
    class Valves(BaseModel):
        MCP_SERVER_URL: str = Field(default="http://host.docker.internal:3434/mcp")
        LLM_MODEL: str = Field(default="")
        REASONING_EFFORT: str = Field(default="high")

    async def pipe(self, body, __user__, __request__, __event_emitter__):
        user_message = body["messages"][-1]["content"]

        # Create LangGraph ReAct agent with MCP tools
        llm = ChatOpenAI(model=self.valves.LLM_MODEL)
        agent = create_react_agent(
            model=llm,
            tools=[list_documents, search_collection],
            state_modifier=FULL_NEO_NL_SYSTEM_PROMPT  # Complete LibreChat prompt
        )

        # Stream agent execution
        async for event in agent.astream({"messages": [{"role": "user", "content": user_message}]}):
            # Handle different event types
            if "agent" in event:
                for msg in event["agent"].get("messages", []):
                    if hasattr(msg, "content") and msg.content:
                        yield msg.content
```

**Alternative: LangGraph State Machine for Explicit Workflow**

For more control than ReAct, use LangGraph's state machine to enforce the LibreChat workflow:

```python
from langgraph.graph import StateGraph, END
from typing import TypedDict, Annotated

class AgentState(TypedDict):
    messages: list
    discovered_docs: list
    relevant_collections: list
    search_results: list
    final_response: str

# Define explicit workflow matching LibreChat
graph = StateGraph(AgentState)
graph.add_node("discover", discover_documents)      # Call list_documents
graph.add_node("analyze", analyze_discovery)        # LLM decides collections
graph.add_node("search", search_collections)        # Call search_collection(s)
graph.add_node("synthesize", generate_response)     # Final LLM response

graph.set_entry_point("discover")
graph.add_edge("discover", "analyze")
graph.add_conditional_edges("analyze", route_to_search_or_respond)
graph.add_edge("search", "synthesize")
graph.add_edge("synthesize", END)

agent = graph.compile()
```

**Comparison Table**

| Aspect | Option A (Enhanced Pipe) | Option D (LangGraph) |
|--------|--------------------------|----------------------|
| Tool decisions | Fixed order | LLM decides autonomously |
| Multi-step reasoning | No | Yes (ReAct loop) |
| Workflow flexibility | Static | Dynamic or state-machine |
| LibreChat parity | ~85% | ~100% |
| Complexity | Low | Medium-High |
| Latency | Lower (2 MCP calls) | Higher (multiple LLM calls) |
| Dependencies | None | langchain, langgraph |
| Debugging | Easy | Requires tracing |

**Pros**:
- True agentic behavior matching LibreChat exactly
- LLM decides when to call list_documents vs search_collection
- Can handle complex multi-step queries
- Reasoning traces can be exposed to user
- State machine variant provides predictable execution

**Cons**:
- Additional dependencies (langchain, langgraph)
- Higher latency due to agent reasoning loops
- More tokens consumed (higher cost)
- More complex to debug (need LangSmith or similar)
- Streaming requires careful event handling

## Required Changes Summary

To achieve LibreChat parity with the current pipe:

1. **Add comprehensive system prompt** - Copy from LibreChat config
2. **Add list_documents call** - Discover documents before searching
3. **Add multi-collection search** - Search relevant collections identified from discovery
4. **Add reasoning_effort parameter** - Set to "high" in LLM payload
5. **Add collection routing logic** - Either from discovery results or enhanced keyword detection

## Code References

- `scripts/pipes/neo_nl_assistant.py` - Current pipe implementation
- `/Users/lexlubbers/Code/soev/genai-utils/api/mcp_server.py:668-801` - list_documents tool
- `/Users/lexlubbers/Code/soev/genai-utils/api/mcp_server.py:844-921` - search_collection tool
- `backend/open_webui/utils/mcp/client.py` - MCPClient implementation
- `backend/open_webui/utils/payload.py:114` - reasoning_effort mapping
- `thoughts/shared/plans/old/2026-01-03-neo-nl-pipe-mcp-integration.md` - Previous implementation plan

## Historical Context

The pipe was developed as part of the NEO NL migration from LibreChat to Open WebUI:
- `thoughts/shared/plans/old/2026-01-03-neo-nl-pipe-mcp-integration.md` - Implementation plan
- `thoughts/shared/research/old/neo-nl-migration-phases.md` - Migration phases research

These documents are archived in `old/` directories, suggesting the current implementation is a working version but may not have complete parity with LibreChat.

## Open Questions

1. **Which option should we implement?**
   - Option A: Enhanced pipe with multi-collection search (~85% parity, low complexity)
   - Option B: Native MCP integration (true agentic, different UX)
   - Option C: Hybrid approach (pipe + native MCP)
   - Option D: LangGraph agentic pipe (~100% parity, higher complexity/cost)

2. **Is the gpt-oss-120b model available via Open WebUI's configured models?**
   - The current pipe uses Open WebUI's internal model routing
   - Need to verify HuggingFace router is configured

3. **Should list_documents be called for every query?**
   - LibreChat prompt says "always use list_documents first"
   - This adds latency but improves relevance

4. **How should we handle the security collection?**
   - LibreChat prompt restricts security queries
   - May need authentication/authorization in the pipe
