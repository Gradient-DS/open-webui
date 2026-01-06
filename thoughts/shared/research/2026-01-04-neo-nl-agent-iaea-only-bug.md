---
date: 2026-01-04T12:00:00+01:00
researcher: Claude
git_commit: 7d753a1ac075c8b029e349f61a5f07e4800317ba
branch: main
repository: open-webui
topic: "Why NEO NL Agent only returns IAEA documents"
tags: [research, neo-nl-agent, routing, collections, bug]
status: complete
last_updated: 2026-01-04
last_updated_by: Claude
---

# Research: Why NEO NL Agent Only Returns IAEA Documents

**Date**: 2026-01-04T12:00:00+01:00
**Researcher**: Claude
**Git Commit**: 7d753a1ac075c8b029e349f61a5f07e4800317ba
**Branch**: main
**Repository**: open-webui

## Research Question

Why does the `scripts/pipes/neo_nl_agent.py` pipe only return IAEA documents instead of also searching anvs, wetten_overheid, and security collections?

## Summary

The issue has **two root causes**:

1. **IAEA-only default fallback** (lines 389 and 393-396): When the LLM response cannot be parsed as JSON, the code defaults to `["iaea"]` only
2. **Router prompt lacks collection descriptions**: The LLM doesn't know what each collection contains (Dutch vs international, legal vs technical), so it cannot make informed routing decisions

## Detailed Findings

### 1. Default Fallback to IAEA Only

Location: `scripts/pipes/neo_nl_agent.py:388-396`

```python
result = json.loads(response_text.strip())
return {
    "query_type": result.get("type", "factual"),
    "target_collections": result.get("collections", ["iaea"]),  # Default: IAEA only!
}
except (json.JSONDecodeError, IndexError):
    log.warning(f"Failed to parse router response: {response[:100]}")
    return {
        "query_type": "factual",
        "target_collections": ["iaea"],  # Fallback: IAEA only!
    }
```

**Problem**: Any parsing failure defaults to IAEA. If the LLM:
- Returns malformed JSON
- Wraps JSON in extra text
- Returns collections in an unexpected format
- Returns empty collections array

...the result is always `["iaea"]`.

### 2. Router Prompt Lacks Collection Descriptions

Location: `scripts/pipes/neo_nl_agent.py:165-178`

**Current prompt:**
```
Collections: anvs, iaea, wetten_overheid, security
```

**Missing information from MCP server** (`genai-utils/api/mcp_server.py:854-858`):
```
- anvs: Dutch Nuclear Safety Authority (ANVS) documents
- iaea: International Atomic Energy Agency publications
- wetten_overheid: Dutch legal texts and regulations
- security: Information and physical security-related documents
```

Without these descriptions, the LLM has no basis for selecting the correct collection. It may default to IAEA because:
- IAEA is mentioned in the example queries
- IAEA is the most recognizable international nuclear authority
- The LLM doesn't know anvs contains Dutch regulatory documents

### 3. IAEA-Biased Examples

The query type examples in the router prompt (lines 167-171) are IAEA-centric:

| Query Type | Example | Collections Implied |
|------------|---------|---------------------|
| factual | "What is IAEA standard X?" | iaea |
| exploratory | "What documents discuss nuclear safety?" | unclear |
| deep_dive | (no example) | n/a |
| comparative | "Compare IAEA and ANVS on X" | iaea, anvs |

There are no examples suggesting when to use `wetten_overheid` (Dutch law) or `security`.

### 4. No Logging of LLM Response on Success

Location: `scripts/pipes/neo_nl_agent.py:392`

Only logs on parse failure:
```python
log.warning(f"Failed to parse router response: {response[:100]}")
```

No logging of successful routes, making it impossible to diagnose why certain collections are/aren't selected.

## Code References

- `scripts/pipes/neo_nl_agent.py:165-178` - Router prompt definition (missing collection descriptions)
- `scripts/pipes/neo_nl_agent.py:364-396` - `route_query` function (handles routing logic)
- `scripts/pipes/neo_nl_agent.py:388-389` - Default to IAEA when collections not in response
- `scripts/pipes/neo_nl_agent.py:393-396` - Fallback to IAEA on JSON parse failure
- `genai-utils/api/mcp_server.py:854-858` - Collection descriptions in MCP server
- `genai-utils/api/config/deployment-neo.yaml:31-55` - Collection definitions (anvs, iaea, wetten_overheid, security)

## Recommended Fixes

### Fix 1: Improve Router Prompt (High Impact)

Replace lines 165-178 with:

```python
ROUTER_PROMPT = """Classify this query and identify which document collections to search.

Query types:
- factual: Asks for specific information (e.g., "What is the IAEA GSR Part 2 standard?")
- exploratory: Asks what's available (e.g., "What documents discuss nuclear safety culture?")
- deep_dive: Requests detailed explanation of specific topic/document
- comparative: Compares multiple sources (e.g., "How do IAEA and Dutch ANVS requirements differ?")

Available collections and their contents:
- anvs: Dutch Nuclear Safety Authority (ANVS) - Dutch regulatory documents, guidelines, licenses
- iaea: International Atomic Energy Agency - International standards, safety guides, technical reports
- wetten_overheid: Dutch legal texts - Laws, regulations, decrees (Kernenergiewet, BKSE, etc.)
- security: Nuclear security documents - Physical protection, information security, threat assessment

Collection selection guidance:
- Dutch regulatory questions → anvs, wetten_overheid
- International standards → iaea
- Legal/compliance questions → wetten_overheid, anvs
- Security topics → security
- General nuclear safety → iaea, anvs
- When in doubt → include multiple relevant collections

Query: {query}

Respond ONLY with valid JSON (no markdown, no explanation):
{{"type": "factual|exploratory|deep_dive|comparative", "collections": ["collection1", ...]}}"""
```

### Fix 2: Improve Default Fallback (Medium Impact)

Replace lines 388-396 with:

```python
result = json.loads(response_text.strip())
collections = result.get("collections", [])
# If no collections specified, search all
if not collections:
    log.warning(f"Router returned no collections, searching all")
    collections = ["anvs", "iaea", "wetten_overheid", "security"]
return {
    "query_type": result.get("type", "factual"),
    "target_collections": collections,
}
except (json.JSONDecodeError, IndexError):
    log.warning(f"Failed to parse router response, searching all collections: {response[:200]}")
    return {
        "query_type": "factual",
        "target_collections": ["anvs", "iaea", "wetten_overheid", "security"],
    }
```

### Fix 3: Add Debug Logging (Low Impact, High Diagnostic Value)

After line 386, add:

```python
log.info(f"[Router] Query: {state['query'][:100]}")
log.info(f"[Router] LLM response: {response}")
log.info(f"[Router] Parsed: type={result.get('type')}, collections={result.get('collections')}")
```

## Architecture Insights

The LangGraph workflow in neo_nl_agent.py follows this pattern:

```
route_query (classify + select collections)
    ├── factual → retrieve_chunks → summarize → END
    └── exploratory/deep_dive/comparative → discover_documents
            ├── deep_dive → read_document → summarize → END
            ├── comparative → retrieve_chunks_parallel → summarize → END
            └── exploratory → retrieve_chunks → summarize → END
```

The routing decision happens **once** at the start. If `route_query` returns only IAEA, subsequent nodes only search IAEA, regardless of query content.

## Testing Recommendation

After implementing fixes, test with these queries to verify all collections are searched:

1. "Wat zijn de ANVS eisen voor kernafval?" → Should include `anvs`
2. "Wat zegt de Kernenergiewet over vergunningen?" → Should include `wetten_overheid`
3. "What are the security requirements for nuclear facilities?" → Should include `security`
4. "Compare IAEA and Dutch requirements for radiation protection" → Should include `iaea`, `anvs`
5. "Geef mij informatie over nucleaire veiligheid" → Should include multiple collections

## Open Questions

1. **Why might JSON parsing fail?** - Need to check what LLM model is being used and whether it reliably outputs JSON
2. **Is the LLM_MODEL valve configured?** - If empty, it auto-selects first non-pipe model which may not be good at JSON output
3. **Should collection selection be deterministic?** - Consider keyword-based fallback instead of LLM-only routing
