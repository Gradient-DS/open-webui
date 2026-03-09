---
date: 2026-03-09T12:00:00+01:00
researcher: Claude
git_commit: 6a14558bf2a53381eb06c25cf552b5cb75132983
branch: feat/agent-api-integration
repository: Gradient-DS/open-webui
topic: "Configurable multi-layered feedback mechanism for MKB version"
tags: [research, codebase, feedback, evaluations, admin-settings, ratings]
status: complete
last_updated: 2026-03-09
last_updated_by: Claude
---

# Research: Configurable Multi-Layered Feedback Mechanism

**Date**: 2026-03-09
**Researcher**: Claude
**Git Commit**: 6a14558bf
**Branch**: feat/agent-api-integration
**Repository**: Gradient-DS/open-webui

## Research Question

How does the current feedback system work in Open WebUI, and what needs to change to support a configurable 3-layer per-turn feedback system + conversation-level feedback, all manageable from the admin panel?

## Summary

The existing feedback system already covers most of Layer 1 (thumbs up/down), Layer 2 (predefined reasons), and Layer 3 (free-text comment). The main gaps are:

1. **No neutral option** in Layer 1 (only thumbs up/down, no neutral face)
2. **Hardcoded reason tags** — the LIKE_REASONS and DISLIKE_REASONS are hardcoded in `RateComment.svelte`, not configurable from admin
3. **No conversation-level feedback** — feedback is per-message only
4. **No admin configuration UI** for feedback layers — the Evaluations settings tab only has arena model config
5. **Rating scale not configurable** — thumbs (binary) and 1-10 are hardcoded

## Detailed Findings

### Current Feedback Architecture

#### Layer 1 — Thumbs Up/Down (exists)

- **Buttons**: `src/lib/components/chat/Messages/ResponseMessage.svelte:1208-1284`
- Binary: +1 (thumbs up) or -1 (thumbs down)
- Gated by `enable_message_rating` feature flag and `chat.rate_response` permission
- Feature flag: `config.py:1669` key `ui.enable_message_rating`

#### Layer 2 — Predefined Reasons (exists, hardcoded)

- **Component**: `src/lib/components/chat/Messages/RateComment.svelte:16-33`
- **LIKE_REASONS** (7 items): accurate_information, followed_instructions_perfectly, showcased_creativity, positive_attitude, attention_to_detail, thorough_explanation, other
- **DISLIKE_REASONS** (8 items): dont_like_the_style, too_verbose, not_helpful, not_factually_correct, didnt_fully_follow_instructions, refused_when_it_shouldnt_have, being_lazy, other
- Also includes a 1-10 detail rating scale (lines 135-148), constrained by thumbs direction

#### Layer 3 — Free Text Comment (exists)

- **Component**: `RateComment.svelte:216-221`
- Simple textarea bound to `comment`
- No custom label/prompt — just a generic comment field

#### Auto-Generated Tags

- When feedback is first given, `generateTags` (`src/lib/apis/index.ts:737-768`) calls the LLM to auto-generate tags
- Tags stored on both `message.annotation.tags` and `feedbackItem.data.tags`

### Data Storage

#### Database Model (`backend/open_webui/models/feedbacks.py:20-30`)

```
feedback table:
  id (Text PK, UUID)
  user_id (Text)
  version (BigInteger)
  type (Text, always "rating")
  data (JSON) → { rating, model_id, sibling_model_ids, reason, comment, tags, details }
  meta (JSON) → { arena, chat_id, message_id, message_index, model_id, base_models }
  snapshot (JSON) → { chat: full_chat_object }
  created_at (BigInteger)
  updated_at (BigInteger)
```

Key: `data` and `meta` use `extra="allow"` Pydantic models, so additional fields are stored without schema changes.

#### Message-Level Storage

Annotation stored on the message object in chat history:
```json
message.annotation = {
  rating: 1 | -1,
  reason: "string",
  comment: "string",
  tags: ["string"],
  details: { rating: 1-10 }
}
```

Linked via `message.feedbackId` → `feedback.id`

### feedbackHandler Flow (`ResponseMessage.svelte:418-534`)

1. Builds updated `message.annotation` with new rating/details
2. Fetches current chat via `getChatById`
3. Constructs `feedbackItem` with `type`, `data`, `meta`, `snapshot`
4. Creates or updates feedback record via API
5. Saves `feedbackId` on message locally
6. Shows `RateComment` panel if initial rating
7. Auto-generates tags via LLM

### Admin Panel — Evaluations

#### Top-Level Evaluations Tab

- Route: `/admin/evaluations/[tab]`
- Component: `src/lib/components/admin/Evaluations.svelte`
- Two sub-tabs: **Leaderboard** and **Feedbacks**
- Leaderboard computes Elo ratings from feedback data (client-side)
- Feedbacks: paginated list with sort, export, delete
- Gated by `FEATURE_ADMIN_EVALUATIONS` env var (config.py:1637)

#### Evaluations Settings Tab

- Route: `/admin/settings/evaluations`
- Component: `src/lib/components/admin/Settings/Evaluations.svelte`
- **Only two settings currently:**
  1. `ENABLE_EVALUATION_ARENA_MODELS` (boolean toggle)
  2. `EVALUATION_ARENA_MODELS` (list of arena model configs)
- No feedback configuration at all

### Config Storage Pattern (`PersistentConfig`)

- Defined at `config.py:165-221`
- Three layers: env var → database → Redis cache
- Arena config stored at `evaluation.arena.enable` and `evaluation.arena.models`
- New feedback config should follow same pattern, e.g.:
  - `evaluation.feedback.enable_layer1` → boolean
  - `evaluation.feedback.layer1_scale` → "thumbs" | "1-3" | "1-5"
  - `evaluation.feedback.enable_layer2` → boolean
  - `evaluation.feedback.layer2_tags` → list of {key, label} dicts
  - `evaluation.feedback.enable_layer3` → boolean
  - `evaluation.feedback.layer3_prompt` → string
  - `evaluation.feedback.enable_conversation_rating` → boolean
  - `evaluation.feedback.conversation_scale` → "1-5" | "1-10"

### Feature Flags

- `enable_message_rating` → `config.py:1669`, key `ui.enable_message_rating` — controls thumbs visibility
- `FEATURE_ADMIN_EVALUATIONS` → `config.py:1637` — controls admin tab visibility
- Both checked at `src/lib/components/chat/Messages/ResponseMessage.svelte:1208`

## What Needs to Change

### Backend Changes

1. **New PersistentConfig values** in `config.py` for all feedback layer settings
2. **New API endpoints** in `evaluations.py`:
   - `GET /feedback-config` — returns feedback layer configuration
   - `POST /feedback-config` — updates feedback layer configuration (admin only)
   - Expose feedback config to non-admin users via the main `/api/config` response
3. **Conversation-level feedback model**: Either extend the existing `feedback` table with a `scope` field ("message" vs "conversation") or create a separate model
4. **New API endpoints for conversation feedback** (or extend existing ones)

### Frontend Changes

1. **`RateComment.svelte`**:
   - Replace hardcoded `LIKE_REASONS`/`DISLIKE_REASONS` with config-driven tags
   - Make the 1-10 scale configurable (could be 1-5, 1-3, etc.)
   - Add neutral option to Layer 1
   - Custom prompt text for Layer 3 (e.g., "Wat had het antwoord moeten zijn?")

2. **New conversation-level feedback component**:
   - Triggered at conversation end (or on navigation away?)
   - 1-5 star rating + free text
   - Needs to determine "end of conversation" trigger

3. **Admin Settings/Evaluations.svelte**:
   - New section: "Feedback Configuration"
   - Toggle enable/disable for each layer
   - Custom tag management UI (add/edit/delete tags with key+label)
   - Scale selector dropdowns
   - Custom prompt text for Layer 3
   - Conversation-level feedback toggle + scale config

4. **`ResponseMessage.svelte`**:
   - Add neutral face button option
   - Read feedback config from store/config to determine which layers to show

## Code References

- `src/lib/components/chat/Messages/ResponseMessage.svelte:418-534` — feedbackHandler
- `src/lib/components/chat/Messages/ResponseMessage.svelte:1208-1284` — thumbs buttons
- `src/lib/components/chat/Messages/RateComment.svelte` — detailed feedback form
- `src/lib/apis/evaluations/index.ts` — frontend API client
- `backend/open_webui/routers/evaluations.py` — backend API routes
- `backend/open_webui/models/feedbacks.py` — feedback database model
- `backend/open_webui/config.py:1573-1592` — arena config
- `backend/open_webui/config.py:1637` — feature flag
- `backend/open_webui/config.py:1669` — message rating toggle
- `backend/open_webui/main.py:1480-1482` — router mounting
- `backend/open_webui/main.py:2023` — feature flag exposure
- `src/lib/components/admin/Settings/Evaluations.svelte` — admin evaluations settings
- `src/lib/components/admin/Evaluations.svelte` — admin evaluations page
- `src/lib/components/admin/Evaluations/Feedbacks.svelte` — feedback list
- `src/lib/components/admin/Evaluations/FeedbackModal.svelte` — feedback detail modal

## Architecture Insights

- **Flexible JSON storage** with `extra="allow"` Pydantic models means we can add new feedback fields without database migrations
- **PersistentConfig** pattern is well-established — new feedback config should follow the same `evaluation.feedback.*` namespace
- **Feature flags** are env-var-only (non-persistent); config toggles use PersistentConfig for runtime admin changes
- The existing `feedback` table can be extended for conversation-level feedback by adding a `scope` discriminator to the `meta` JSON field — no migration needed

## Open Questions

1. **Conversation end detection**: How to determine when to show conversation-level feedback? Options: explicit "end chat" button, navigation away, idle timeout, or always visible at bottom
2. **Neutral rating storage**: Currently rating is 1 or -1. Neutral could be 0, but need to check if any code assumes only 1/-1
3. **Tag localization**: Should tag labels be stored as i18n keys or raw strings? Raw strings are simpler but lock to one language
4. **Migration strategy**: Should we extend the existing `feedback` table or create a separate `conversation_feedback` table?
5. **Config exposure**: Feedback config needs to be available to non-admin users (for rendering the UI). Currently evaluations config is admin-only — need a public endpoint or include in the main config response
