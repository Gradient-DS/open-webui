# NEO NL Pipe Internal Model Routing - Implementation Plan

## Overview

Refactor the NEO NL Document Assistant pipe to use Open WebUI's internal `generate_chat_completion` function instead of making direct HTTP calls to LLM APIs. This simplifies configuration and leverages Open WebUI's existing provider infrastructure.

## Current State Analysis

**Current Implementation** (`scripts/pipes/neo_nl_assistant.py` v0.7.1):
- Makes direct HTTP calls using `aiohttp` to LLM APIs
- Requires separate configuration: `LLM_API_KEY`, `LLM_API_BASE_URL`, `LLM_MODEL`
- Environment variable fallbacks: `NEO_NL_LLM_*` → `OPENAI_*`
- Manual SSE parsing and streaming handling
- Provider-specific quirks (`max_tokens` vs `max_completion_tokens` for OpenAI)

**Key Files:**
- `scripts/pipes/neo_nl_assistant.py:179-243` - Current `_stream_llm_response` with aiohttp
- `scripts/pipes/neo_nl_assistant.py:146-177` - Current `_get_llm_config` with env var logic
- `backend/open_webui/utils/chat.py:164` - `generate_chat_completion` function
- `backend/open_webui/functions.py:254-278` - Available pipe parameters

## Desired End State

A simplified NEO NL pipe that:
1. Uses Open WebUI's `generate_chat_completion()` for LLM calls
2. Has a single `LLM_MODEL` Valve to select from Open WebUI's configured models
3. No separate API keys or endpoints needed
4. Automatic handling of all provider quirks (streaming, token limits, etc.)
5. Continues to work with MCP search and citation display

### Verification:
- Select "NEO NL Document Assistant" in model dropdown
- Ask a question about nuclear safety
- Response streams correctly with citations
- Works with any model configured in Open WebUI (OpenAI, Ollama, HuggingFace, etc.)

## What We're NOT Doing

- Changing the MCP search logic (stays the same)
- Changing the citation/source event emission (stays the same)
- Changing the system prompt or RAG approach
- Adding new features (just refactoring LLM calls)

## Implementation Approach

Replace the direct `aiohttp` HTTP calls with Open WebUI's internal `generate_chat_completion` function, which handles:
- Provider routing (OpenAI, Ollama, custom pipes)
- Authentication and API keys
- Streaming response handling
- Provider-specific parameter differences

---

## Phase 1: Simplify Valves Configuration

### Overview
Remove LLM API configuration Valves (API key, base URL) and keep only the model selector.

### Changes Required:

#### 1. Update Valves Class
**File**: `scripts/pipes/neo_nl_assistant.py`

Remove these Valves:
- `LLM_API_BASE_URL`
- `LLM_API_KEY`

Keep/Update:
- `LLM_MODEL` - Change default and description to reference Open WebUI models

```python
class Valves(BaseModel):
    # MCP Server Configuration
    MCP_SERVER_URL: str = Field(
        default="http://host.docker.internal:3434/mcp",
        description="URL of the genai-utils MCP server"
    )

    # LLM Configuration - uses Open WebUI's configured models
    LLM_MODEL: str = Field(
        default="",
        description="Model ID from Open WebUI (leave empty to use default model)"
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
```

### Success Criteria:

#### Automated Verification:
- [x] Python syntax check passes: `python3 -m py_compile scripts/pipes/neo_nl_assistant.py`

#### Manual Verification:
- [ ] Valves appear correctly in Admin UI with updated descriptions

---

## Phase 2: Update Imports and Function Parameters

### Overview
Add required imports for internal routing and update the `pipe()` function signature to receive `__request__`.

### Changes Required:

#### 1. Update Imports
**File**: `scripts/pipes/neo_nl_assistant.py`

Remove:
```python
import aiohttp
```

Add:
```python
from starlette.responses import StreamingResponse
from open_webui.utils.chat import generate_chat_completion
from open_webui.models.users import Users
```

#### 2. Update pipe() Function Signature
**File**: `scripts/pipes/neo_nl_assistant.py`

```python
async def pipe(
    self,
    body: dict,
    __user__: dict = None,
    __task__: str = None,
    __request__ = None,  # Add this - required for generate_chat_completion
    __event_emitter__=None,
) -> AsyncGenerator[str, None]:
```

### Success Criteria:

#### Automated Verification:
- [x] Python syntax check passes: `python3 -m py_compile scripts/pipes/neo_nl_assistant.py`
- [x] No import errors when loading the function

---

## Phase 3: Replace LLM Streaming with Internal Routing

### Overview
Replace the `_stream_llm_response` method with a new `_call_llm` method that uses `generate_chat_completion`.

### Changes Required:

#### 1. Remove Old Methods
**File**: `scripts/pipes/neo_nl_assistant.py`

Delete:
- `_get_llm_config()` method (lines 146-177)
- `_stream_llm_response()` method (lines 179-243)

#### 2. Add New LLM Call Method
**File**: `scripts/pipes/neo_nl_assistant.py`

```python
async def _call_llm(
    self,
    messages: list[dict],
    __user__: dict,
    __request__,
) -> AsyncGenerator[str, None]:
    """Call LLM using Open WebUI's internal routing."""

    if not __request__:
        yield "Error: Request context not available. Cannot call LLM."
        return

    if not __user__:
        yield "Error: User context not available. Cannot call LLM."
        return

    # Get user object for the API call
    user = Users.get_user_by_id(__user__.get("id"))
    if not user:
        yield "Error: User not found."
        return

    # Determine which model to use
    model_id = self.valves.LLM_MODEL
    if not model_id:
        # Use the first available model from Open WebUI
        models = __request__.app.state.MODELS
        if models:
            model_id = next(iter(models.keys()))
        else:
            yield "Error: No models configured in Open WebUI."
            return

    # Build the request payload
    payload = {
        "model": model_id,
        "messages": messages,
        "stream": True,
    }

    log.info(f"[NEO NL Pipe] Calling LLM via Open WebUI: model={model_id}")

    try:
        response = await generate_chat_completion(
            request=__request__,
            form_data=payload,
            user=user,
            bypass_filter=True,  # Skip access control for internal calls
        )

        # Handle streaming response
        if isinstance(response, StreamingResponse):
            async for chunk in response.body_iterator:
                # Parse SSE format: "data: {...}\n\n"
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
            # Non-streaming response
            content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
            yield content
        else:
            yield str(response)

    except Exception as e:
        log.error(f"[NEO NL Pipe] LLM call failed: {e}")
        yield f"Error calling LLM: {str(e)}"
```

#### 3. Update Main pipe() Method
**File**: `scripts/pipes/neo_nl_assistant.py`

Update the calls from `_stream_llm_response` to `_call_llm`:

For system tasks:
```python
if __task__ and __task__ in self.SYSTEM_TASKS:
    log.info(f"[NEO NL Pipe] System task '{__task__}', skipping RAG")
    llm_messages = [{"role": "user", "content": user_message}]
    async for chunk in self._call_llm(llm_messages, __user__, __request__):
        yield chunk
    return
```

For main RAG flow:
```python
# Stream LLM response
async for chunk in self._call_llm(llm_messages, __user__, __request__):
    yield chunk
```

### Success Criteria:

#### Automated Verification:
- [x] Python syntax check passes: `python3 -m py_compile scripts/pipes/neo_nl_assistant.py`
- [ ] Function saves without errors in Admin UI

#### Manual Verification:
- [ ] Query returns a streaming response
- [ ] Response uses the configured Open WebUI model
- [ ] No API key configuration needed in Valves

---

## Phase 4: Update Environment Variable Handling

### Overview
Remove NEO_NL_LLM_* environment variables from documentation since they're no longer needed.

### Changes Required:

#### 1. Update .env.neo.example
**File**: `.env.neo.example`

Remove/simplify the NEO NL section:
```bash
# ------------------------------------------------------------
# NEO NL DOCUMENT ASSISTANT (Pipe Function)
# ------------------------------------------------------------
# The NEO NL pipe uses Open WebUI's configured models.
# Configure models in the Open WebUI Admin UI or via OPENAI_* environment variables.
#
# To use a specific model for NEO NL, set the LLM_MODEL valve in:
# Admin -> Functions -> neo_nl_assistant -> Settings
```

### Success Criteria:

#### Automated Verification:
- [x] .env.neo.example is valid

---

## Phase 5: Cleanup and Version Bump

### Overview
Remove unused imports, update version, and clean up the code.

### Changes Required:

#### 1. Final Cleanup
**File**: `scripts/pipes/neo_nl_assistant.py`

- Remove `import os` if no longer used
- Remove `import aiohttp`
- Update version to `0.8.0`
- Update requirements (remove `aiohttp` if not needed elsewhere)

```python
"""
title: NEO NL Document Assistant
description: Search nuclear safety documents via MCP and generate responses with citations
author: NEO NL Team
version: 0.8.0
"""
```

Note: Keep `aiohttp` in requirements if MCP client needs it, otherwise remove.

### Success Criteria:

#### Automated Verification:
- [x] Python syntax check passes: `python3 -m py_compile scripts/pipes/neo_nl_assistant.py`
- [x] No unused imports

#### Manual Verification:
- [ ] Pipe loads correctly after update
- [ ] Full end-to-end test: query → MCP search → LLM response with citations
- [ ] System tasks (follow-ups, titles) work correctly
- [ ] Works with different Open WebUI models (test with at least 2)

---

## Testing Strategy

### Unit Tests:
- Not applicable (pipe stored in database)

### Integration Tests:
- Verify MCP server connectivity (unchanged)
- Verify LLM routing through Open WebUI
- End-to-end query flow

### Manual Testing Steps:
1. Register updated pipe:
   ```bash
   python scripts/pipes/register_neo_nl_pipe.py --url http://localhost:8080 --token YOUR_TOKEN --update
   ```
2. Select "NEO NL Document Assistant" in model dropdown
3. Test query: "Wat zijn de IAEA veiligheidsrichtlijnen?"
4. Verify:
   - Status messages appear (Zoeken → Genereren → Voltooid)
   - Response streams correctly
   - Citations appear with clickable URLs
   - Follow-up suggestions generate without errors
5. Change LLM_MODEL valve to a different model and test again

---

## Rollback Plan

If issues arise:
1. The previous version (0.7.1) is in git history
2. Can revert by re-registering the old version
3. Environment variables still work as fallback if needed

---

## References

- Open WebUI Pipe documentation: https://docs.openwebui.com/features/plugin/functions/pipe/
- `generate_chat_completion`: `backend/open_webui/utils/chat.py:164`
- Pipe parameter injection: `backend/open_webui/functions.py:254-278`
- Current NEO NL pipe: `scripts/pipes/neo_nl_assistant.py`

---

*Created: 2026-01-03*
*Based on: NEO NL Pipe v0.7.1 refactoring discussion*
