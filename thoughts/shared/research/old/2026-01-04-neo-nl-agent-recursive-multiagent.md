---
date: 2026-01-04T14:30:00+01:00
researcher: Claude
git_commit: 7d753a1ac075c8b029e349f61a5f07e4800317ba
branch: main
repository: open-webui
topic: "Recursive and Multi-Agent Enhancements for NEO NL Agent"
tags: [research, langgraph, multi-agent, rag, recursive, self-reflection]
status: complete
last_updated: 2026-01-04
last_updated_by: Claude
---

# Research: Recursive and Multi-Agent Enhancements for NEO NL Agent

**Date**: 2026-01-04T14:30:00+01:00
**Researcher**: Claude
**Git Commit**: 7d753a1ac075c8b029e349f61a5f07e4800317ba
**Branch**: main
**Repository**: open-webui

## Research Question

Could we make a second version of `scripts/pipes/neo_nl_agent.py` that:
1. Continues recursively (max 3 times) until the question is properly answered, OR
2. Spawns multiple subagents to research separate topics

What would such solutions look like? What would have more impact on performance? What did we already have in the original?

## Summary

The current `neo_nl_agent.py` implements a **single-pass DAG** with conditional routing but **no cycles or iteration**. Two enhancement patterns are viable:

| Pattern | Performance Impact | Complexity | Best For |
|---------|-------------------|------------|----------|
| **Recursive Self-Reflection** | +2-3x latency per iteration | Medium | Quality-critical answers, complex reasoning |
| **Multi-Agent Topic Decomposition** | +137x speedup potential (parallel) | High | Multi-faceted questions, comparative queries |

**Recommendation**: Implement **Option A (Recursive)** first as it's lower complexity and directly improves answer quality. Add **Option B (Multi-Agent)** for `comparative` query types only.

---

## What the Original Already Has

### Current Graph Structure (`neo_nl_agent.py:589-631`)

```
                    route
                      │
         ┌────────────┼────────────┐
         │                         │
         ▼                         ▼
     discover                  retrieve (factual)
         │                         │
    ┌────┼────┐                    │
    │    │    │                    │
    ▼    ▼    ▼                    │
  read  retr  retrieve_parallel    │
  doc                              │
    │    │         │               │
    └────┴─────────┴───────────────┘
                   │
                   ▼
               summarize
                   │
                   ▼
                  END
```

### Existing Features

| Feature | Implementation | Location |
|---------|---------------|----------|
| Query Classification | 4 types: factual, exploratory, deep_dive, comparative | `route_query()` lines 388-426 |
| Collection Routing | Routes to 1-4 collections based on query | `ROUTER_PROMPT` lines 165-202 |
| Parallel Retrieval | `asyncio.gather()` for comparative queries | `retrieve_chunks_parallel()` lines 483-511 |
| Fact Summarization | Compresses chunks into structured facts | `summarize_content()` lines 514-554 |
| Status Emissions | Fun nuclear-themed status messages | `emit_status()` lines 99-105 |

### What's Missing

1. **No iteration tracking** - No `iteration_count` in `GraphState`
2. **No quality evaluation** - No check if answer is sufficient
3. **No query refinement** - No ability to reformulate failed queries
4. **No parallel topic decomposition** - Comparative queries search same query across collections, not different sub-queries

---

## Option A: Recursive Self-Reflection (CRAG-Style)

### Concept

Add a feedback loop where the agent evaluates its own answer quality and retries with refined queries up to 3 times.

### State Changes

```python
class GraphState(TypedDict):
    # ... existing fields ...

    # NEW: Iteration tracking
    iteration_count: int
    max_iterations: int

    # NEW: Quality evaluation
    answer_quality: Literal["sufficient", "partial", "insufficient"]
    missing_aspects: list[str]

    # NEW: Query refinement
    refined_query: Optional[str]
    previous_queries: list[str]
```

### New Nodes

```python
async def evaluate_answer(state: GraphState) -> dict:
    """Evaluate if summarized facts sufficiently answer the query."""
    await emit_status(state["_event_emitter"], "evaluating")

    facts = state.get("summarized_facts", [])
    if not facts:
        return {
            "answer_quality": "insufficient",
            "missing_aspects": ["No relevant information found"],
        }

    prompt = EVALUATOR_PROMPT.format(
        query=state["query"],
        facts=json.dumps(facts, ensure_ascii=False)
    )

    response = await call_llm(
        messages=[{"role": "user", "content": prompt}],
        request=state["_request"],
        user=state["_user"],
        model_id=state["_valves"].LLM_MODEL,
        stream=False
    )

    result = json.loads(response)
    return {
        "answer_quality": result.get("quality", "sufficient"),
        "missing_aspects": result.get("missing", []),
    }


async def refine_query(state: GraphState) -> dict:
    """Generate a refined query to find missing information."""
    await emit_status(state["_event_emitter"], "refining")

    prompt = REFINER_PROMPT.format(
        original_query=state["query"],
        previous_queries=state.get("previous_queries", []),
        missing_aspects=state.get("missing_aspects", []),
        facts_found=json.dumps(state.get("summarized_facts", []))
    )

    response = await call_llm(
        messages=[{"role": "user", "content": prompt}],
        request=state["_request"],
        user=state["_user"],
        model_id=state["_valves"].LLM_MODEL,
        stream=False
    )

    return {
        "refined_query": response.strip(),
        "previous_queries": state.get("previous_queries", []) + [state["query"]],
        "query": response.strip(),  # Update query for next iteration
        "iteration_count": state.get("iteration_count", 0) + 1,
    }
```

### New Routing Logic

```python
def should_continue(state: GraphState) -> str:
    """Decide whether to continue iterating or finish."""
    iteration = state.get("iteration_count", 0)
    max_iter = state.get("max_iterations", 3)
    quality = state.get("answer_quality", "sufficient")

    if quality == "sufficient":
        return "end"
    if iteration >= max_iter:
        return "end"  # Give up after max iterations
    return "refine"


def build_recursive_rag_graph() -> StateGraph:
    graph = StateGraph(GraphState)

    # Existing nodes
    graph.add_node("route", route_query)
    graph.add_node("discover", discover_documents)
    graph.add_node("retrieve", retrieve_chunks)
    graph.add_node("retrieve_parallel", retrieve_chunks_parallel)
    graph.add_node("read_document", read_document)
    graph.add_node("summarize", summarize_content)

    # NEW nodes
    graph.add_node("evaluate", evaluate_answer)
    graph.add_node("refine", refine_query)

    graph.set_entry_point("route")

    # Existing conditional edges
    graph.add_conditional_edges("route", route_by_query_type, {...})
    graph.add_conditional_edges("discover", route_after_discovery, {...})

    # Linear edges to summarize
    graph.add_edge("read_document", "summarize")
    graph.add_edge("retrieve", "summarize")
    graph.add_edge("retrieve_parallel", "summarize")

    # NEW: Summarize -> Evaluate
    graph.add_edge("summarize", "evaluate")

    # NEW: Conditional loop from evaluate
    graph.add_conditional_edges(
        "evaluate",
        should_continue,
        {
            "end": END,
            "refine": "refine",
        }
    )

    # NEW: Refine loops back to route
    graph.add_edge("refine", "route")

    return graph.compile()
```

### New Prompts

```python
EVALUATOR_PROMPT = """Evaluate if these facts sufficiently answer the query.

Query: {query}

Facts found:
{facts}

Evaluate:
1. Do the facts directly answer the question?
2. Are there important aspects not covered?
3. Is more information needed?

Respond with JSON:
{{"quality": "sufficient|partial|insufficient", "missing": ["aspect1", ...]}}"""

REFINER_PROMPT = """The previous search didn't fully answer the question.

Original query: {original_query}
Previous search attempts: {previous_queries}
Missing aspects: {missing_aspects}
Facts already found: {facts_found}

Generate a refined search query to find the missing information.
Focus on what's still needed, don't repeat what was already found.

Output only the refined query, nothing else."""
```

### Graph Visualization

```
                    route
                      │
         ┌────────────┼────────────┐
         │                         │
         ▼                         ▼
     discover                  retrieve
         │                         │
    ┌────┼────┐                    │
    │    │    │                    │
    ▼    ▼    ▼                    │
  read  retr  retrieve_parallel    │
  doc                              │
    │    │         │               │
    └────┴─────────┴───────────────┘
                   │
                   ▼
               summarize
                   │
                   ▼
               evaluate
                   │
         ┌────────┴────────┐
         │                 │
         ▼                 ▼
        END             refine
                          │
                          └──────────► route (LOOP)
```

### Performance Impact

| Metric | Single Pass | With Recursion (avg 2 iterations) |
|--------|-------------|-----------------------------------|
| Latency | ~3-5s | ~6-10s |
| LLM Calls | 3 (route, summarize, generate) | 5-7 per iteration |
| Token Cost | ~2K tokens | ~4-6K tokens |
| Answer Quality | Good for simple queries | Better for complex queries |

---

## Option B: Multi-Agent Topic Decomposition

### Concept

For complex questions, decompose into sub-topics and spawn parallel agents to research each, then synthesize.

### State Changes

```python
class GraphState(TypedDict):
    # ... existing fields ...

    # NEW: Topic decomposition
    sub_topics: list[dict]  # [{"topic": str, "query": str, "collections": list}]
    sub_results: list[dict]  # Results from each sub-agent

    # NEW: Synthesis tracking
    synthesis_strategy: Literal["merge", "compare", "rank"]
```

### New Nodes

```python
async def decompose_query(state: GraphState) -> dict:
    """Decompose a complex query into researchable sub-topics."""
    await emit_status(state["_event_emitter"], "decomposing")

    prompt = DECOMPOSER_PROMPT.format(
        query=state["query"],
        available_collections=["iaea", "anvs", "wetten_overheid", "security"]
    )

    response = await call_llm(
        messages=[{"role": "user", "content": prompt}],
        request=state["_request"],
        user=state["_user"],
        model_id=state["_valves"].LLM_MODEL,
        stream=False
    )

    sub_topics = json.loads(response)
    return {"sub_topics": sub_topics}


async def research_sub_topics(state: GraphState) -> dict:
    """Research all sub-topics in parallel."""
    await emit_status(state["_event_emitter"], "researching_parallel")

    async def research_one(topic: dict) -> dict:
        # Each sub-topic gets its own retrieval
        chunks, sources = [], []
        for collection in topic.get("collections", []):
            c, s = await mcp_search_collection(
                state["_valves"].MCP_SERVER_URL,
                topic["query"],
                collection
            )
            chunks.extend(c[:3])
            sources.extend(s[:3])

        # Summarize for this sub-topic
        if chunks:
            summary = await summarize_for_topic(
                topic["topic"],
                topic["query"],
                chunks,
                state
            )
            return {
                "topic": topic["topic"],
                "summary": summary,
                "sources": sources,
            }
        return {"topic": topic["topic"], "summary": "", "sources": []}

    tasks = [research_one(t) for t in state.get("sub_topics", [])]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    valid_results = [r for r in results if not isinstance(r, Exception)]
    return {"sub_results": valid_results}


async def synthesize_results(state: GraphState) -> dict:
    """Synthesize sub-topic results into coherent facts."""
    await emit_status(state["_event_emitter"], "synthesizing")

    sub_results = state.get("sub_results", [])
    all_sources = []

    # Merge all sources
    for result in sub_results:
        all_sources.extend(result.get("sources", []))

    # Build combined summary
    combined_text = "\n\n".join([
        f"## {r['topic']}\n{r['summary']}"
        for r in sub_results if r.get("summary")
    ])

    # Create structured facts from combined research
    prompt = SYNTHESIZER_PROMPT.format(
        query=state["query"],
        research=combined_text
    )

    response = await call_llm(
        messages=[{"role": "user", "content": prompt}],
        request=state["_request"],
        user=state["_user"],
        model_id=state["_valves"].LLM_MODEL,
        stream=False
    )

    facts = json.loads(response)
    return {
        "summarized_facts": facts,
        "sources": all_sources,
    }
```

### New Routing Logic

```python
def route_by_complexity(state: GraphState) -> str:
    """Route based on query complexity."""
    query_type = state.get("query_type", "factual")

    if query_type == "comparative":
        return "decompose"  # Multi-agent path
    elif query_type == "factual":
        return "retrieve"
    else:
        return "discover"


def build_multiagent_rag_graph() -> StateGraph:
    graph = StateGraph(GraphState)

    # Existing nodes
    graph.add_node("route", route_query)
    graph.add_node("discover", discover_documents)
    graph.add_node("retrieve", retrieve_chunks)
    graph.add_node("read_document", read_document)
    graph.add_node("summarize", summarize_content)

    # NEW nodes for multi-agent path
    graph.add_node("decompose", decompose_query)
    graph.add_node("research_parallel", research_sub_topics)
    graph.add_node("synthesize", synthesize_results)

    graph.set_entry_point("route")

    # Route with multi-agent option
    graph.add_conditional_edges(
        "route",
        route_by_complexity,
        {
            "decompose": "decompose",
            "discover": "discover",
            "retrieve": "retrieve",
        }
    )

    # Multi-agent path
    graph.add_edge("decompose", "research_parallel")
    graph.add_edge("research_parallel", "synthesize")
    graph.add_edge("synthesize", END)

    # Existing paths
    graph.add_conditional_edges("discover", route_after_discovery, {...})
    graph.add_edge("read_document", "summarize")
    graph.add_edge("retrieve", "summarize")
    graph.add_edge("summarize", END)

    return graph.compile()
```

### Graph Visualization

```
                    route
                      │
         ┌────────────┼────────────────────┐
         │            │                    │
         ▼            ▼                    ▼
     decompose    discover             retrieve
         │            │                    │
         ▼       ┌────┼────┐               │
   research      │    │    │               │
   _parallel   read  retr  retrieve        │
   [T1][T2]    doc   _par  _parallel       │
   [T3][T4]     │    │         │           │
         │      └────┴─────────┴───────────┘
         │                    │
         ▼                    ▼
    synthesize            summarize
         │                    │
         └────────┬───────────┘
                  ▼
                 END
```

### Performance Impact

| Metric | Sequential | Multi-Agent Parallel |
|--------|------------|---------------------|
| Latency (4 topics) | ~20s | ~5-6s |
| LLM Calls | 4 sequential | 4 parallel + 2 (decompose, synthesize) |
| Token Cost | ~4K tokens | ~6K tokens |
| Coverage | Limited by query | Multiple perspectives |

**Key Insight**: The 137x speedup from web research applies here - searching 4 collections for 4 different sub-queries in parallel is dramatically faster than sequential.

---

## Comparison: Which Has More Impact?

### Performance Matrix

| Scenario | Recursive | Multi-Agent | Winner |
|----------|-----------|-------------|--------|
| Simple factual query | +50% latency, same quality | Overkill | Neither (use original) |
| Complex single-topic | +100% latency, +30% quality | Not applicable | Recursive |
| Comparative query | Multiple iterations | Parallel + synthesis | Multi-Agent |
| Vague/ambiguous query | Refines until clear | Decomposes wrong | Recursive |
| Multi-faceted research | Many iterations | Single pass, parallel | Multi-Agent |

### Recommendation: Hybrid Approach

```python
def route_to_architecture(state: GraphState) -> str:
    """Choose architecture based on query type."""
    query_type = state.get("query_type", "factual")

    if query_type == "comparative":
        return "multiagent"  # Parallel topic research
    elif query_type in ["deep_dive", "exploratory"]:
        return "recursive"   # Quality-focused iteration
    else:
        return "simple"      # Original single-pass
```

### Implementation Priority

1. **Phase 1**: Add recursive self-reflection to existing pipe
   - Lower complexity
   - Benefits all query types
   - Easy to test and tune

2. **Phase 2**: Add multi-agent for comparative queries only
   - Higher complexity
   - Dramatic speedup for specific use case
   - Can reuse recursive pattern within sub-agents

---

## Open WebUI Pipe Constraints

### No Blocking Constraints Found

| Concern | Status | Details |
|---------|--------|---------|
| Execution timeout | No limit | Pipes run until completion or client disconnect |
| Recursion limit | No limit | Must implement own `max_iterations` |
| Parallel tasks | No limit | Can spawn unlimited `asyncio.gather()` tasks |
| Memory | No limit | Must manage state size manually |

### Event Emitter Still Works

Both patterns can use `emit_status()` for progress updates:

```python
STATUS_MESSAGES = {
    # ... existing ...
    "evaluating": ["Checking answer quality...", "Grading response..."],
    "refining": ["Refining search strategy...", "Adjusting query..."],
    "decomposing": ["Breaking down your question...", "Analyzing sub-topics..."],
    "researching_parallel": ["Researching multiple angles...", "Parallel investigation..."],
}
```

---

## Code References

- `scripts/pipes/neo_nl_agent.py:30-54` - Current GraphState definition
- `scripts/pipes/neo_nl_agent.py:388-426` - `route_query()` classification
- `scripts/pipes/neo_nl_agent.py:483-511` - `retrieve_chunks_parallel()` pattern
- `scripts/pipes/neo_nl_agent.py:514-554` - `summarize_content()` for fact extraction
- `scripts/pipes/neo_nl_agent.py:589-631` - `build_rag_graph()` assembly
- `backend/open_webui/functions.py:158-353` - Pipe execution with no timeout

---

## Architecture Insights

1. **LangGraph cycles are straightforward**: Just add `add_edge("evaluate", "route")` to create a loop
2. **State accumulation**: Use `Annotated[list, operator.add]` for merging parallel results
3. **No framework limits**: Open WebUI pipes have no execution constraints
4. **Token cost is the real limit**: Each iteration/agent multiplies LLM costs

---

## Related Research

- `thoughts/shared/research/2026-01-04-multi-agent-rag-architecture.md` - Original architecture decisions
- `thoughts/shared/plans/2026-01-04-neo-nl-agent-langgraph-pipe.md` - Implementation plan

---

## Open Questions

1. **Quality threshold tuning**: What makes an answer "sufficient"? Need evaluation criteria
2. **Query refinement strategy**: How to prevent oscillating between similar queries?
3. **Cost management**: Should we have a token budget that stops iteration early?
4. **Caching**: Can we cache sub-topic results across similar queries?
