---
date: 2026-03-26T17:00:00+02:00
researcher: Claude Code
git_commit: c0db5407764a3c167d1c971ee01b26284e7c2e28
branch: dev
repository: Gradient-DS/open-webui
topic: "Vanilla Open WebUI Pipeline Spec — What the LLM Receives Under Each Config"
tags: [research, codebase, agent-api, pipeline, rag, web-search, code-interpreter, prompts, middleware]
status: complete
last_updated: 2026-03-26
last_updated_by: Claude Code
---

# Vanilla Open WebUI Pipeline Spec

**Purpose**: Exact specification of what Open WebUI does to messages before/after the LLM call, so an external agent can replicate the same behavior.

## Pipeline Overview

```
User message arrives
│
├─ 1. apply_params_to_form_data()     — merge model params into request
├─ 2. Load messages from DB           — reconstruct history with output items
├─ 3. System prompt processing        — user/model/folder system prompts
├─ 4. Convert URL images to base64    — inline remote images
├─ 5. Pipeline inlet filters          — custom filter functions (inlet)
├─ 6. Feature handlers (pre-request):
│    ├─ a. Voice mode prompt          — if features.voice
│    ├─ b. Memory injection           — if features.memory
│    ├─ c. Web search                 — if features.web_search
│    ├─ d. Image generation prompt    — if features.image_generation
│    └─ e. Code interpreter prompt    — if features.code_interpreter
├─ 7. Skills injection                — model + user-selected skills
├─ 8. Knowledge flattening            — model-attached KBs → file items
├─ 9. Tool resolution & calling       — tool_ids → resolve → LLM picks → execute
├─10. RAG file processing             — files → query generation → vector search → sources
├─11. Source context injection         — sources → RAG template → inject into messages
│
╰─── LLM CALL ───
│
├─12. Response streaming              — parse tags (reasoning, code_interpreter)
├─13. Code interpreter execution      — extract code → run → inject output → re-prompt LLM
├─14. Pipeline outlet filters         — custom filter functions (outlet)
├─15. Post-response tasks:
│    ├─ a. Title generation
│    ├─ b. Tag generation
│    └─ c. Follow-up generation
└─16. Chat persistence                — save to DB
```

---

## Step-by-Step Detail

### 1. System Prompt Assembly

**Sources of system prompts** (applied in order, each appends or replaces):

| Source | Method | When |
|--------|--------|------|
| User/model system prompt (from `messages[0]` if role=system) | `replace_system_message_content()` | Always |
| Folder "Project" system prompt | `add_or_update_system_message()` (append) | Chat is in a folder with `system_prompt` |
| Voice mode prompt | `add_or_update_system_message()` (overwrite) | `features.voice = true` |
| Memory context | `add_or_update_system_message()` (append) | `features.memory = true` |
| RAG context (when `RAG_SYSTEM_CONTEXT=true`) | `add_or_update_system_message()` (append) | Sources found from RAG |

**Note:** `add_or_update_system_message(text, messages, append=True)` appends to existing system message. With `append=False` or `replace=True` it overwrites.

### 2. Memory Injection

**Trigger:** `features.memory = true` AND `function_calling != "native"`

**What happens:**
1. Query the memory vector store with the last user message, `k=3`
2. Format results as numbered list with dates:
   ```
   User Context:
   1. [2026-03-15] User prefers Python for scripting
   2. [2026-03-20] User works on healthcare domain
   3. [2026-01-10] User has experience with FastAPI
   ```
3. Append to system message via `add_or_update_system_message(..., append=True)`

**Config:** Memory store is ChromaDB-based, configured via admin panel. The `k=3` is hardcoded.

### 3. Web Search

**Trigger:** `features.web_search = true` AND `function_calling != "native"`

**What happens (3 sub-steps):**

#### 3a. Query Generation
- Uses the **task model** (configured `TASK_MODEL` or falls back to chat model)
- Prompt template: `QUERY_GENERATION_PROMPT_TEMPLATE` (default below)
- Input: last 6 messages of chat history
- Output: `{"queries": ["query1", "query2", ...]}`
- If generation fails or returns empty: falls back to raw user message

**Default query generation prompt:**
```
### Task:
Analyze the chat history to determine the necessity of generating search queries,
in the given language. By default, **prioritize generating 1-3 broad and relevant
search queries** unless it is absolutely certain that no additional information
is required.

### Guidelines:
- Respond **EXCLUSIVELY** with a JSON object.
- Format: { "queries": ["query1", "query2"] }
- If no search needed: { "queries": [] }
- Today's date is: {{CURRENT_DATE}}.

### Chat History:
<chat_history>
{{MESSAGES:END:6}}
</chat_history>
```

#### 3b. Web Search Execution
- Engine: configured `WEB_SEARCH_ENGINE` (Google PSE, Brave, SearXNG, etc.)
- Runs all queries in parallel (or with semaphore if `WEB_SEARCH_CONCURRENT_REQUESTS` set)
- Returns URLs + snippets

#### 3c. Web Content Processing (two modes)
- **If `BYPASS_WEB_SEARCH_WEB_LOADER = true`**: Use search snippets directly as docs
- **If `BYPASS_WEB_SEARCH_WEB_LOADER = false`** (default): Load full page content via web loader
- **If `BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL = true`**: Pass raw docs directly to RAG template
- **If `BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL = false`** (default): Embed docs into vector DB collection, then retrieve via similarity search (step 10)

Result: web search adds file items to `form_data["files"]` with `type: "web_search"`.

### 4. Code Interpreter Prompt

**Trigger:** `features.code_interpreter = true` AND `function_calling != "native"`

**What happens:** Appends prompt to the last user message via `add_or_update_user_message()`:

```
#### Code Interpreter

You have access to a Python code interpreter via:
`<code_interpreter type="code" lang="python"></code_interpreter>`

- The Python shell runs directly in the user's browser for fast execution...
- **You must enclose your code within `<code_interpreter type="code" lang="python">` XML tags**
- Always print meaningful outputs
- After obtaining output, provide a concise analysis
...
```

If engine is `pyodide` (default), also appends:
```
##### Pyodide Environment
- This Python environment runs via Pyodide in the browser.
- User-uploaded files are available at `/mnt/uploads/`.
- Use `import os; os.listdir('/mnt/uploads')` to discover available files.
```

### 5. Tool Resolution & Calling (non-native FC)

**Trigger:** `tool_ids` present AND `function_calling != "native"`

**What happens:**
1. Resolve tool IDs → tool specs + callable functions (includes MCP servers)
2. Build tool-calling prompt from `TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE`:
   ```
   Available Tools: {{TOOLS}}
   Your task is to choose and return the correct tool(s)...
   Return JSON: {"tool_calls": [{"name": "...", "parameters": {...}}]}
   ```
3. Send to task model for tool selection
4. Parse response → execute selected tools → collect results as sources
5. Tool results become citation sources (same as RAG sources)

### 6. RAG File Processing

**Trigger:** `files` present in metadata (from KB attachments, web search results, uploaded files)

**What happens:**

#### 6a. Retrieval Query Generation
- Same query generation system as web search (step 3a)
- Prompt: `QUERY_GENERATION_PROMPT_TEMPLATE` with `type: "retrieval"`
- Config: `ENABLE_RETRIEVAL_QUERY_GENERATION` (default true)
- Falls back to raw user message if disabled or fails

#### 6b. Vector Search
`get_sources_from_items()` processes each file item:
- **`type: "collection"`** — query vector DB collection(s)
- **`type: "web_search"`** — query web search collection
- **`type: "text"`** — use raw text or collection
- **`type: "note"`** / **`type: "chat"`** — load content from DB

Search parameters (all configurable via admin panel):
| Parameter | Config Key | Default |
|-----------|-----------|---------|
| Top-K results | `TOP_K` | 5 |
| Top-K after reranking | `TOP_K_RERANKER` | 3 |
| Relevance threshold | `RELEVANCE_THRESHOLD` | 0.0 |
| Hybrid search (BM25) | `ENABLE_RAG_HYBRID_SEARCH` | false |
| BM25 weight | `HYBRID_BM25_WEIGHT` | 0.3 |
| Full context mode | `RAG_FULL_CONTEXT` | false |

#### 6c. Reranking (optional)
If `RERANKING_FUNCTION` is configured (e.g., Cohere reranker, cross-encoder):
- Takes query + retrieved documents
- Reranks and filters to `TOP_K_RERANKER`

### 7. Source Context Injection (RAG Template)

**What happens:** All sources (from RAG, web search, tools) are formatted and injected.

#### Format sources as XML:
```xml
<source id="1" name="document.pdf">Content of chunk...</source>
<source id="2" name="web-result.html">Content of chunk...</source>
```

#### Apply RAG template:
The default template wraps sources with instructions:
```
### Task:
Respond to the user query using the provided context, incorporating inline
citations in the format [id] **only when the <source> tag includes an explicit
id attribute**.

### Guidelines:
- If you don't know the answer, clearly state that.
- Respond in the same language as the user's query.
- Only include inline citations using [id] when the <source> tag includes an id.

### Example of Citation:
"According to the study, the proposed method increases efficiency by 20% [1]."

<context>
{{CONTEXT}}
</context>
```

#### Injection location (configurable):
- **If `RAG_SYSTEM_CONTEXT = true`**: Appended to system message
- **If `RAG_SYSTEM_CONTEXT = false`** (default): Replaces last user message content

### 8. Image Generation Prompt

**Trigger:** `features.image_generation = true` AND `function_calling != "native"`

Appends image generation instructions to user message.

### 9. Voice Mode Prompt

**Trigger:** `features.voice = true`

Overwrites system message with voice-optimized prompt:
```
You are a friendly, concise voice assistant.
Everything you say will be spoken aloud.
Keep responses short, clear, and natural.
...
```

---

## Post-Response Processing

### Code Interpreter Execution (in `process_chat_response`)

**Trigger:** `features.code_interpreter = true` AND LLM outputs `<code_interpreter>` tags

**What happens:**
1. Stream parser detects `<code_interpreter type="code" lang="python">` opening tag
2. Accumulates code until `</code_interpreter>` closing tag
3. Sanitizes code, applies blocked module restrictions
4. Executes via one of two engines:
   - **Pyodide** (default): Sends `execute:python` event via Socket.IO → frontend runs in browser web worker → returns stdout/stderr/result
   - **Jupyter**: Backend calls `execute_code_jupyter()` to remote Jupyter kernel
5. Output (stdout, images, results) is attached to the code_interpreter output item
6. If LLM generated more code_interpreter blocks, repeats (up to 5 retries)
7. Converts base64 images to uploaded file URLs

**Key for external agent:** The agent must output `<code_interpreter type="code" lang="python">...</code_interpreter>` tags. Open WebUI's `process_chat_response` will parse and execute them. The agent does NOT need to handle execution itself.

### Title Generation

- Uses task model (not chat model, usually)
- Template: `TITLE_GENERATION_PROMPT_TEMPLATE`
- Input: message history
- Output: `{"title": "..."}`
- Runs only on first message or when explicitly enabled

### Tag Generation

- Uses task model
- Template: `TAGS_GENERATION_PROMPT_TEMPLATE`
- Input: message history
- Output: `{"tags": ["tag1", "tag2"]}`

### Follow-up Generation

- Uses task model
- Template: `FOLLOW_UPS_GENERATION_PROMPT_TEMPLATE`
- Input: message history
- Output: `{"follow_ups": ["question1", "question2"]}`

---

## What the External Agent Receives (via AgentPayload)

When `AGENT_API_ENABLED=true`, the agent receives:

```json
{
  "agent": "AGENT_API_AGENT env var",
  "model": "selected-model-id",
  "messages": [
    // System prompt already set (user/model config)
    // Memory already injected (if features.memory)
    // Voice prompt already applied (if features.voice)
    // Code interpreter prompt SKIPPED (after our fix)
    // Image gen prompt still applied (if features.image_generation)
    // Web search prompt NOT injected (Open WebUI has no separate web search prompt)
    // RAG context NOT injected (skipped)
    // Tool results NOT injected (skipped)
  ],
  "features": {
    "web_search": true,      // agent should do its own search
    "memory": true,          // already injected by OWUI
    "code_interpreter": true, // agent should output <code_interpreter> tags
    "image_generation": false,
    "voice": false
  },
  "files": [...],            // raw file metadata (not processed into RAG)
  "knowledge": [             // raw KB references
    {
      "id": "kb-uuid",
      "name": "My KB",
      "type": "local",
      "collection_names": ["kb-uuid"]
    }
  ],
  "tool_ids": ["tool1", "server:mcp:server1"],
  "rag_filter": {...},
  "temperature": 0.7,
  "top_p": 0.9,
  "max_tokens": 4096
}
```

---

## Agent Implementation Checklist (to replicate vanilla OWUI)

The agent needs to replicate these steps that are now bypassed:

### Must implement:
1. **Web search** (if `features.web_search`):
   - Generate search queries from conversation (use same prompt template or own)
   - Execute web search (same engine or own)
   - Load page content (or use snippets)
   - Either embed+retrieve or use directly

2. **RAG retrieval** (if `knowledge` or `files` present):
   - Generate retrieval queries from conversation
   - Query vector DB collections from `knowledge[].collection_names`
   - Apply reranking if available
   - Format results as `<source>` tags

3. **Source context injection**:
   - Use the same RAG template format (or equivalent)
   - Inject `<source id="N" name="...">content</source>` into context
   - Instruct LLM to cite with `[N]` format

4. **Tool calling** (if `tool_ids` present):
   - Resolve tools from tool_ids
   - Present tools to LLM
   - Execute selected tools
   - Inject results as sources

5. **Code interpreter output format**:
   - When code execution is appropriate, output `<code_interpreter type="code" lang="python">code</code_interpreter>`
   - OWUI handles execution and output display

### Already handled by OWUI (agent receives pre-processed):
- System prompt (from model/user config)
- Memory injection (already in messages)
- Voice mode prompt (already in messages)
- Image generation prompt — **note: still injected, consider skipping this too**
- Skills injection (already in messages)

### Handled by OWUI post-response (agent doesn't need to):
- Title generation
- Tag generation
- Follow-up generation
- Chat persistence
- WebSocket streaming

---

## SSE Events the Agent Should Emit

To match the OWUI UX, the agent should emit these Socket.IO-compatible SSE events:

```
# Status updates (shown in UI as progress indicators)
event: status
data: {"description": "Generating search queries...", "done": false}

event: status
data: {"action": "web_search", "description": "Searching the web", "done": false}

event: status
data: {"action": "web_search_queries_generated", "queries": ["q1", "q2"], "done": false}

event: status
data: {"action": "web_search", "description": "Searched {{count}} sites", "urls": [...], "done": true}

event: status
data: {"action": "knowledge_search", "query": "user question", "done": false}

event: status
data: {"action": "queries_generated", "queries": ["q1"], "done": false}

event: status
data: {"action": "sources_retrieved", "count": 5, "done": true}

event: status
data: {"action": "knowledge_search", "query": "user question", "done": true}

# Source citations (rendered as citation chips in UI)
event: source
data: {"name": "document.pdf", "url": "...", "id": "source-id"}

# Standard OpenAI streaming chunks
data: {"choices": [{"delta": {"content": "The answer is..."}}]}

# End of stream
data: [DONE]
```

---

## Key Config Parameters the Agent Should Be Aware Of

| Parameter | Env Var / Config Key | Default | Effect |
|-----------|---------------------|---------|--------|
| RAG template | `RAG_TEMPLATE` | See above | How sources are presented to LLM |
| Top-K | `TOP_K` | 5 | Number of chunks retrieved |
| Reranker Top-K | `TOP_K_RERANKER` | 3 | After reranking |
| Relevance threshold | `RELEVANCE_THRESHOLD` | 0.0 | Minimum score |
| Hybrid search | `ENABLE_RAG_HYBRID_SEARCH` | false | BM25 + vector |
| Full context | `RAG_FULL_CONTEXT` | false | Return full docs, not chunks |
| Query generation enabled | `ENABLE_RETRIEVAL_QUERY_GENERATION` | true | Generate queries vs raw message |
| Search query gen enabled | `ENABLE_SEARCH_QUERY_GENERATION` | true | For web search |
| Web search engine | `WEB_SEARCH_ENGINE` | — | Which provider |
| Task model | `TASK_MODEL` | — | Model for query gen, title, etc. |
| Embedding model | `RAG_EMBEDDING_MODEL` | sentence-transformers | For vector search |
| Reranking model | `RAG_RERANKING_MODEL` | — | For reranking |
| Code interpreter engine | `CODE_INTERPRETER_ENGINE` | pyodide | pyodide or jupyter |

---

## Appendix: Full Prompt Templates

All defaults from `backend/open_webui/config.py`. Each can be overridden via the corresponding `PersistentConfig` (admin panel) or env var.

### A1. RAG Template (`RAG_TEMPLATE`)

```
### Task:
Respond to the user query using the provided context, incorporating inline citations in the format [id] **only when the <source> tag includes an explicit id attribute** (e.g., <source id="1">).

### Guidelines:
- If you don't know the answer, clearly state that.
- If uncertain, ask the user for clarification.
- Respond in the same language as the user's query.
- If the context is unreadable or of poor quality, inform the user and provide the best possible answer.
- If the answer isn't present in the context but you possess the knowledge, explain this to the user and provide the answer using your own understanding.
- **Only include inline citations using [id] (e.g., [1], [2]) when the <source> tag includes an id attribute.**
- Do not cite if the <source> tag does not contain an id attribute.
- Do not use XML tags in your response.
- Ensure citations are concise and directly related to the information provided.

### Example of Citation:
If the user asks about a specific topic and the information is found in a source with a provided id attribute, the response should include the citation like in the following example:
* "According to the study, the proposed method increases efficiency by 20% [1]."

### Output:
Provide a clear and direct response to the user's query, including inline citations in the format [id] only when the <source> tag with id attribute is present in the context.

<context>
{{CONTEXT}}
</context>
```

**Injection:** If `RAG_SYSTEM_CONTEXT=true` → appended to system message. If `false` (default) → replaces last user message.

### A2. Query Generation (`QUERY_GENERATION_PROMPT_TEMPLATE`)

Used for both web search and RAG retrieval query generation.

```
### Task:
Analyze the chat history to determine the necessity of generating search queries, in the given language. By default, **prioritize generating 1-3 broad and relevant search queries** unless it is absolutely certain that no additional information is required. The aim is to retrieve comprehensive, updated, and valuable information even with minimal uncertainty. If no search is unequivocally needed, return an empty list.

### Guidelines:
- Respond **EXCLUSIVELY** with a JSON object. Any form of extra commentary, explanation, or additional text is strictly prohibited.
- When generating search queries, respond in the format: { "queries": ["query1", "query2"] }, ensuring each query is distinct, concise, and relevant to the topic.
- If and only if it is entirely certain that no useful results can be retrieved by a search, return: { "queries": [] }.
- Err on the side of suggesting search queries if there is **any chance** they might provide useful or updated information.
- Be concise and focused on composing high-quality search queries, avoiding unnecessary elaboration, commentary, or assumptions.
- Today's date is: {{CURRENT_DATE}}.
- Always prioritize providing actionable and broad queries that maximize informational coverage.

### Output:
Strictly return in JSON format:
{
  "queries": ["query1", "query2"]
}

### Chat History:
<chat_history>
{{MESSAGES:END:6}}
</chat_history>
```

**Template variables:**
- `{{CURRENT_DATE}}` — today's date
- `{{MESSAGES:END:6}}` — last 6 messages from chat history

### A3. Code Interpreter Prompt (`CODE_INTERPRETER_PROMPT_TEMPLATE`)

Appended to the **last user message**.

```
#### Code Interpreter

You have access to a Python code interpreter via: `<code_interpreter type="code" lang="python"></code_interpreter>`

- The Python shell runs directly in the user's browser for fast execution of analysis, calculations, or problem-solving. Use it in this response.
- You can use a wide array of libraries for data manipulation, visualization, API calls, or any computational task. Think outside the box and harness Python's full potential.
- **You must enclose your code within `<code_interpreter type="code" lang="python">` XML tags** and stop right away. If you don't, the code won't execute.
- Do NOT use triple backticks (```py ... ```) inside the XML tags — that is markdown formatting, not executable Python code.
- **Always print meaningful outputs** (results, tables, summaries, visuals). Avoid implicit outputs; use explicit print statements.
- After obtaining output, **provide a concise analysis, interpretation, or next steps** to help the user understand the findings.
- If results are unclear or unexpected, refine the code and re-execute. Iterate until you deliver meaningful insights.
- **If a link to an image, audio, or any file appears in the output, display it exactly as-is** in your response so the user can access it. Do not modify the link.
- Respond in the chat's primary language. Default to English if multilingual.

Ensure the code interpreter is effectively utilized to achieve the highest-quality analysis for the user.
```

**Pyodide addendum** (appended when engine = `pyodide`, not `jupyter`):

```
##### Pyodide Environment

- This Python environment runs via Pyodide in the browser. **Do not install packages** — `pip install`, `subprocess`, and `micropip.install()` are not available.
- If a required library is unavailable, use an alternative approach with available modules. Do not attempt to install anything.

##### Persistent File System

- User-uploaded files are available at `/mnt/uploads/`. When the user asks you to work with their files, read from this directory.
- You can also write output files to `/mnt/uploads/` so the user can access and download them from the file browser.
- The file system persists across code executions within the same session.
- Use `import os; os.listdir('/mnt/uploads')` to discover available files.
```

### A4. Voice Mode Prompt (`VOICE_MODE_PROMPT_TEMPLATE`)

**Overwrites** the system message (not append).

```
You are a friendly, concise voice assistant.

Everything you say will be spoken aloud.
Keep responses short, clear, and natural.

STYLE:
- Use simple words and short sentences.
- Sound warm and conversational.
- Avoid long explanations, lists, or complex phrasing.

BEHAVIOR:
- Give the quickest helpful answer first.
- Offer extra detail only if needed.
- Ask for clarification only when necessary.

VOICE OPTIMIZATION:
- Break information into small, easy-to-hear chunks.
- Avoid dense wording or anything that sounds like reading text.

ERROR HANDLING:
- If unsure, say so briefly and offer options.
- If something is unsafe or impossible, decline kindly and suggest a safe alternative.

Stay consistent, helpful, and easy to listen to.
```

### A5. Tools Function Calling (`TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE`)

```
Available Tools: {{TOOLS}}

Your task is to choose and return the correct tool(s) from the list of available tools based on the query. Follow these guidelines:

- Return only the JSON object, without any additional text or explanation.

- If no tools match the query, return an empty array:
   {
     "tool_calls": []
   }

- If one or more tools match the query, construct a JSON response containing a "tool_calls" array with objects that include:
   - "name": The tool's name.
   - "parameters": A dictionary of required parameters and their corresponding values.

The format for the JSON response is strictly:
{
  "tool_calls": [
    {"name": "toolName1", "parameters": {"key1": "value1"}},
    {"name": "toolName2", "parameters": {"key2": "value2"}}
  ]
}
```

### A6. Title Generation (`TITLE_GENERATION_PROMPT_TEMPLATE`)

Post-response, handled by OWUI (not the agent).

```
### Task:
Generate a concise, 3-5 word title with an emoji summarizing the chat history.
### Guidelines:
- The title should clearly represent the main theme or subject of the conversation.
- Use emojis that enhance understanding of the topic, but avoid quotation marks or special formatting.
- Write the title in the chat's primary language; default to English if multilingual.
- Prioritize accuracy over excessive creativity; keep it clear and simple.
- Your entire response must consist solely of the JSON object, without any introductory or concluding text.
- The output must be a single, raw JSON object, without any markdown code fences or other encapsulating text.
- Ensure no conversational text, affirmations, or explanations precede or follow the raw JSON output, as this will cause direct parsing failure.
### Output:
JSON format: { "title": "your concise title here" }
### Examples:
- { "title": "📉 Stock Market Trends" },
- { "title": "🍪 Perfect Chocolate Chip Recipe" },
- { "title": "Evolution of Music Streaming" },
- { "title": "Remote Work Productivity Tips" },
- { "title": "Artificial Intelligence in Healthcare" },
- { "title": "🎮 Video Game Development Insights" }
### Chat History:
<chat_history>
{{MESSAGES:END:2}}
</chat_history>
```

### A7. Tags Generation (`TAGS_GENERATION_PROMPT_TEMPLATE`)

Post-response, handled by OWUI.

```
### Task:
Generate 1-3 broad tags categorizing the main themes of the chat history, along with 1-3 more specific subtopic tags.

### Guidelines:
- Start with high-level domains (e.g. Science, Technology, Philosophy, Arts, Politics, Business, Health, Sports, Entertainment, Education)
- Consider including relevant subfields/subdomains if they are strongly represented throughout the conversation
- If content is too short (less than 3 messages) or too diverse, use only ["General"]
- Use the chat's primary language; default to English if multilingual
- Prioritize accuracy over specificity

### Output:
JSON format: { "tags": ["tag1", "tag2", "tag3"] }

### Chat History:
<chat_history>
{{MESSAGES:END:6}}
</chat_history>
```

### A8. Follow-up Generation (`FOLLOW_UP_GENERATION_PROMPT_TEMPLATE`)

Post-response, handled by OWUI.

```
### Task:
Suggest 3-5 relevant follow-up questions or prompts that the user might naturally ask next in this conversation as a **user**, based on the chat history, to help continue or deepen the discussion.
### Guidelines:
- Write all follow-up questions from the user's point of view, directed to the assistant.
- Make questions concise, clear, and directly related to the discussed topic(s).
- Only suggest follow-ups that make sense given the chat content and do not repeat what was already covered.
- If the conversation is very short or not specific, suggest more general (but relevant) follow-ups the user might ask.
- Use the conversation's primary language; default to English if multilingual.
- Response must be a JSON object with a "follow_ups" key containing an array of strings, no extra text or formatting.
### Output:
JSON format: { "follow_ups": ["Question 1?", "Question 2?", "Question 3?"] }
### Chat History:
<chat_history>
{{MESSAGES:END:6}}
</chat_history>
```

### A9. Image Prompt Generation (`IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE`)

Used when `features.image_generation = true`.

```
### Task:
Generate a detailed prompt for am image generation task based on the given language and context. Describe the image as if you were explaining it to someone who cannot see it. Include relevant details, colors, shapes, and any other important elements.

### Guidelines:
- Be descriptive and detailed, focusing on the most important aspects of the image.
- Avoid making assumptions or adding information not present in the image.
- Use the chat's primary language; default to English if multilingual.
- If the image is too complex, focus on the most prominent elements.

### Output:
Strictly return in JSON format:
{
    "prompt": "Your detailed description here."
}

### Chat History:
<chat_history>
{{MESSAGES:END:6}}
</chat_history>
```

### A10. Memory Injection Format

Not a prompt template — hardcoded format in `chat_memory_handler()`:

```
User Context:
1. [2026-03-15] Memory entry text here
2. [2026-03-20] Another memory entry
3. [2026-01-10] Third memory entry
```

Appended to system message via `add_or_update_system_message(..., append=True)`.

### A11. Source Context Format

Not a prompt template — built by `get_source_context()`:

```xml
<source id="1" name="document.pdf">Content of retrieved chunk...</source>
<source id="2" name="web-result.html">Content of another chunk...</source>
```

This XML string becomes `{{CONTEXT}}` in the RAG template (A1).
