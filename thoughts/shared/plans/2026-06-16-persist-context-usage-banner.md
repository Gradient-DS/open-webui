# Persist Context-Usage Banner Across Reloads Implementation Plan

## Overview

The agent backend (genai-utils) emits a once-per-turn `context_usage` estimate that drives a banner above the chat input ("X% of the context window used…"). Today that value lives only in an in-memory Svelte variable and is lost on page reload until the next turn. This plan persists it onto the assistant message — server-side, mirroring the existing `subagents` pattern — so the banner re-renders on reload and chat navigation.

## Current State Analysis

**The event flow (working today):**

1. **genai-utils** computes `ContextUsage(tokens_used, tokens_budget, fraction)` at the end of each agent turn and dispatches a LangGraph SSE custom event named `context_usage`. It is a computed metric, never written to the agent's event ledger — ephemeral on that side.
2. **OWUI backend** (`backend/open_webui/utils/agent.py:558`) relays it over Socket.IO as `{'type': 'context_usage', 'data': {tokens_used, tokens_budget, fraction}}`. It does not persist it.
3. **OWUI frontend** (`src/lib/components/chat/Chat.svelte:638`, inside `chatEventHandler`) writes it to a standalone component variable `contextUsage` (declared at `:199`). `ContextUsageBanner.svelte` renders off that variable (`Chat.svelte:3511`); the banner is visible once `fraction >= 0.45`.

**Why it is lost on reload / navigation:**

- `contextUsage` is a plain component variable. It is reset to `null` on new chat (`Chat.svelte:1297`) and on navigation into a chat (`:237`), and is **never re-populated on load** because nothing about it is saved.
- On the **agent path the frontend does not save the chat on completion**: `chatCompletedHandler` (`Chat.svelte:1727–1735`) only refreshes the sidebar — its comment states *"Backend handles outlet filters and persistence inline."* So a value living only in the frontend's in-memory history would never be written by the turn that produced it.
- A later frontend-driven save (feature toggle reactive block at `Chat.svelte:307–318`, edits, regenerate → `saveChatHandler` at `:3168`) pushes the full `history` blob via `updateChatById`. If `contextUsage` were not also present on the in-memory message object, such a save would **clobber** any value the backend had persisted.

### Key Discoveries

- **Established precedent — `subagents`**: `backend/open_webui/utils/agent.py:466–487`. `body_generator` accumulates `subagent_events: list[dict]` during the stream and, on the `done` SSE event, persists them onto the message:
  ```python
  await Chats.upsert_message_to_chat_by_id_and_message_id(
      chat_id, message_id, {'subagents': subagent_events}
  )
  ```
  The comment states the exact intent: *"Accumulate every `event: subagent` payload so the message's persisted `subagents` field carries the full lifecycle for rehydration on reload."* This is the correct model to mirror for `context_usage`.
- **Upsert is merge-safe, no migration**: `Chats.upsert_message_to_chat_by_id_and_message_id` (`backend/open_webui/models/chats.py:558–596`) merges the passed dict into the existing message (`{**existing, **message}`, lines 574–577), writes it into the `history.messages[id]` JSON blob, **and** dual-writes the normalized `chat_message` table (lines 586–592). The field lives inside JSON `data`, so no schema column / Alembic migration is needed.
- **Frontend handler already has the message in scope**: the `context_usage` branch (`Chat.svelte:638`) lives inside `chatEventHandler`, which holds `message = history.messages[event.message_id]` (`:540`) and writes it back at `:759`. Stamping `message.contextUsage` is a one-line addition that rides the existing write-back.
- **History round-trips arbitrary fields**: `loadChat` sets `history = chatContent.history` (`Chat.svelte:1567–1570`); the backend stores the `chat` dict verbatim (`chats.py:312/349`); `createMessagesList` (`src/lib/utils/index.ts:1341–1355`) pushes message objects by reference, preserving any field. So a stamped `contextUsage` survives both the blob and the message list.
- **Live-handler precedent for clobber-safety**: the `subagents` live handler stamps `message.subagents` in the frontend (`Chat.svelte:751–756`) in addition to backend persistence — the same belt-and-suspenders we need.
- **Banner payload shape**: `ContextUsageBanner.svelte` expects `{ tokens_used, tokens_budget, fraction }` (`:13–17`). The relayed SSE `data` already has exactly this shape.

## Desired End State

After a turn that produced a `context_usage` event:

- The assistant message carries a persisted `contextUsage` field (`{ tokens_used, tokens_budget, fraction }`) in the DB (history blob + `chat_message` table).
- On full page reload **or** navigating away and back into the chat, the banner re-renders immediately from the persisted value — no need to send a new message.
- Non-agent models (which never emit `context_usage`) and temporary chats are unaffected: banner remains hidden / live-only respectively, no regressions.

**Verification**: send a message to the soev agent until the banner appears → reload the page → banner is still shown with the same percentage.

## What We're NOT Doing

- No new DB column / Alembic migration (the value rides existing JSON storage).
- No change to how genai-utils computes or dispatches `context_usage`.
- No change to the banner's thresholds, copy, or render location.
- No new user-facing strings, therefore no i18n additions.
- Not making the banner reactive to branch-switching beyond what exists today (it reflects the active branch's latest turn, consistent with current behavior).
- Not removing the standalone `contextUsage` variable (it still drives the live, mid-stream banner update).

## Implementation Approach

Mirror the proven `subagents` mechanism end-to-end:

- **Backend is the source of truth** for the agent path — accumulate the latest `context_usage` payload during the stream and persist it on `done`, combined with the existing subagents upsert into a single write.
- **Frontend stamps live** for in-memory consistency (so any frontend-driven save preserves the field rather than clobbering it) and **rehydrates** the banner variable on load from the persisted message field.

Persist under the camelCase key **`contextUsage`** (consistent with `subagents` / `statusHistory` / `uiBlocks`) so the frontend reads it directly without remapping.

---

## Phase 1: Backend persistence

### Overview
Accumulate the latest `context_usage` payload in `body_generator` and persist it onto the assistant message on the `done` event, combined with the existing `subagents` upsert.

### Changes Required

#### 1. Accumulate the latest payload
**File**: `backend/open_webui/utils/agent.py` (near `subagent_events` declaration, ~line 471)
**Changes**: Add a holder for the most recent context-usage payload.

```python
subagent_events: list[dict] = []
# [Gradient] Latest post-turn context-budget estimate. Persisted onto the
# message on `done` so the banner rehydrates on reload (mirrors subagents).
last_context_usage: dict | None = None
```

#### 2. Capture it in the relay handler
**File**: `backend/open_webui/utils/agent.py` (the `context_usage` branch, ~lines 558–574)
**Changes**: After emitting to Socket.IO (live banner), retain the payload for persistence. Latest wins across multi-iteration turns.

```python
if sse_event.event_type == 'context_usage':
    # [Gradient] Post-turn context-budget estimate from the agent service.
    # ... existing comment ...
    last_context_usage = sse_event.data
    if event_emitter:
        try:
            await event_emitter(
                {
                    'type': 'context_usage',
                    'data': sse_event.data,
                }
            )
        except Exception as e:
            log.warning(f'Error emitting context_usage event: {e}')
    continue
```

#### 3. Persist on `done` (single combined upsert)
**File**: `backend/open_webui/utils/agent.py` (the `done` branch, ~lines 474–487)
**Changes**: Build one merged `updates` dict so subagents and contextUsage are written in a single upsert.

```python
if sse_event.event_type == 'done':
    updates: dict[str, Any] = {}
    if subagent_events:
        updates['subagents'] = subagent_events
    if last_context_usage is not None:
        updates['contextUsage'] = last_context_usage
    if updates:
        chat_id = metadata.get('chat_id')
        message_id = metadata.get('message_id')
        if chat_id and message_id:
            try:
                await Chats.upsert_message_to_chat_by_id_and_message_id(
                    chat_id,
                    message_id,
                    updates,
                )
            except Exception as e:
                log.warning(f'Error persisting message updates: {e}')
    break
```

### Success Criteria

#### Automated Verification
- [x] Backend lints: `cd open-webui && npm run lint:backend` (pylint E-checks clean, 10.00/10; black --check unchanged)
- [x] Backend imports/compiles: `cd open-webui && python -c "import backend.open_webui.utils.agent"` (or `open-webui dev` starts without error) — verified via `python -m py_compile`

#### Manual Verification
- [ ] After an agent turn that triggers the banner, the persisted chat (DB `chat.history.messages[<id>]`) contains a `contextUsage` object with `tokens_used`, `tokens_budget`, `fraction`.
- [ ] The existing `subagents` persistence still works for a bezwaar/SubAgent turn (single upsert did not regress it).

**Implementation Note**: After Phase 1 automated verification passes, pause for manual confirmation (inspect a saved chat's message JSON) before proceeding to Phase 2.

---

## Phase 2: Frontend stamp + rehydrate

### Overview
Stamp `message.contextUsage` in the live handler (clobber-safety + in-memory consistency) and rehydrate the `contextUsage` banner variable in `loadChat` from the active branch's latest message bearing the field.

### Changes Required

#### 1. Stamp the live value onto the message
**File**: `src/lib/components/chat/Chat.svelte` (the `context_usage` branch, ~lines 638–652)
**Changes**: Set both the standalone variable (live banner, unchanged) and the message field (persistence parity). The write-back at `:759` carries it into `history.messages[...]`.

```js
} else if (type === 'context_usage') {
    // [Gradient] Post-turn context-budget estimate from the agent
    // service. Drives the banner above the chat input. Also stamped
    // onto the message so a frontend-driven history save preserves it
    // (backend persists it on `done`; this keeps the in-memory blob in
    // sync). Mirrors the subagents handler below.
    if (
        typeof data?.tokens_used === 'number' &&
        typeof data?.tokens_budget === 'number' &&
        typeof data?.fraction === 'number'
    ) {
        const usage = {
            tokens_used: data.tokens_used,
            tokens_budget: data.tokens_budget,
            fraction: data.fraction
        };
        contextUsage = usage;
        message.contextUsage = usage;
    }
}
```

#### 2. Rehydrate the banner on load
**File**: `src/lib/components/chat/Chat.svelte` (in `loadChat`, after the "Mark all non-current assistant messages as done" block, ~after line 1625)
**Changes**: Walk the active branch and take the latest message that carries `contextUsage`.

```js
// [Gradient] Rehydrate the context-usage banner from the persisted
// message field (set by the agent backend on `done`). Reflects the
// active branch's most recent turn; null when no agent turn recorded it.
const branch = createMessagesList(history, history.currentId);
for (let i = branch.length - 1; i >= 0; i--) {
    if (branch[i]?.contextUsage) {
        contextUsage = branch[i].contextUsage;
        break;
    }
}
```

> `createMessagesList` is already imported in `Chat.svelte` (`:64`).

### Success Criteria

#### Automated Verification
- [x] Type-check passes: `cd open-webui && npm run check` — no new errors from my edits (verified: zero errors reference `usageBranch`/`contextUsage`; only pre-existing baseline errors remain in `Chat.svelte`)
- [x] Frontend lint passes: `cd open-webui && npm run lint:frontend` — `eslint` on `Chat.svelte` shows only the file's pre-existing baseline; none in my edited ranges

#### Manual Verification
- [ ] Send messages to the soev agent until the banner appears, then **reload** — banner persists with the same percentage.
- [ ] Navigate to another chat and back — banner reflects the persisted value (not reset to hidden).
- [ ] Send a message in a model that does not emit `context_usage` — no banner, no console errors, no regression.
- [ ] Temporary chat: banner shows live during the turn and is (expectedly) gone after reload, with no errors.
- [ ] Toggle a chat feature (e.g. web search) after a banner-bearing turn, then reload — the feature-toggle save did not clobber `contextUsage`.

**Implementation Note**: Pause for manual confirmation of the reload behavior before considering the work complete.

---

## Phase 3: Verification & cleanup

### Overview
Final pass across the full criteria; confirm no regressions to the established `subagents` reload path (the shared upsert).

### Success Criteria

#### Automated Verification
- [x] `cd open-webui && npm run check` — clean for changed code (pre-existing baseline only)
- [x] `cd open-webui && npm run lint:frontend` — clean for changed code (pre-existing baseline only)
- [x] `cd open-webui && npm run lint:backend` — pylint 10.00/10, black unchanged

#### Manual Verification
- [ ] Reload persistence confirmed for the agent path (primary goal).
- [ ] SubAgent (bezwaar) turn still rehydrates its `subagents` cards on reload — shared upsert unaffected.
- [ ] No new console/server warnings during a normal agent turn.

---

## Testing Strategy

### Manual Testing Steps
1. Open a chat backed by the soev agent. Send enough messages to push `fraction` past 0.45 so the banner appears (warning copy appears at 0.75).
2. Reload the browser. Confirm the banner re-renders immediately with the same percentage (no new message required).
3. Inspect the persisted chat: the latest assistant message JSON has `contextUsage: { tokens_used, tokens_budget, fraction }`.
4. Navigate to a different chat and back; confirm the banner reflects the persisted value.
5. Repeat with a non-agent model and a temporary chat to confirm no regression.
6. Run a SubAgent (bezwaar) turn and reload to confirm `subagents` persistence still works after the combined upsert.

### Regression Watch
- The `done`-branch refactor now upserts when **either** `subagents` or `contextUsage` is present (previously subagents-only). Verify a subagents-only turn and a context_usage-only turn both persist correctly.

## Performance Considerations

- One additional message field (~3 numbers) per banner-bearing turn; negligible payload/storage impact.
- Persistence is folded into the existing `done` upsert — **no extra DB round-trip** beyond what `subagents` already incurs.
- Frontend rehydration is a single reverse walk of the active branch in `loadChat` — O(branch length), trivial.

## Migration Notes

- No migration. Existing chats simply have no `contextUsage` on their messages → banner stays hidden on load until a new agent turn records one. No backfill needed or desired.

## References

- Backend relay + subagents precedent: `backend/open_webui/utils/agent.py:466–487`, context_usage relay at `:558`
- Merge-safe upsert: `backend/open_webui/models/chats.py:558–596`
- Frontend event handler: `src/lib/components/chat/Chat.svelte:537` (`chatEventHandler`), context_usage branch `:638`, write-back `:759`
- Banner component: `src/lib/components/chat/ContextUsageBanner.svelte`
- Banner variable + resets: `Chat.svelte:199`, `:237`, `:1297`; render `:3511`
- Load path: `Chat.svelte:1536` (`loadChat`), `history` assignment `:1567`
- `createMessagesList`: `src/lib/utils/index.ts:1341`
