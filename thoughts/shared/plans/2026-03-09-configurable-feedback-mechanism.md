# Configurable Multi-Layered Feedback Mechanism — Implementation Plan

## Overview

Upgrade the feedback system to support a configurable 3-layer per-turn feedback structure and a new conversation-level feedback component, all manageable from the admin panel's Evaluations settings tab.

## Current State Analysis

**What exists:**
- Layer 1 (thumbs up/down): Works, `ResponseMessage.svelte:1208-1284`
- Layer 2 (reasons): Hardcoded `LIKE_REASONS`/`DISLIKE_REASONS` in `RateComment.svelte:16-33`, not configurable
- Layer 3 (free text): Generic comment textarea in `RateComment.svelte:216-221`, no custom prompt
- Admin Evaluations settings: Only arena model config (`Settings/Evaluations.svelte`)
- Backend: `PersistentConfig` values for arena only (`config.py:1573-1582`)
- Feedback stored in `feedback` table with flexible JSON columns (`extra="allow"`)

**What's missing:**
- Admin-configurable issue tags (replacing hardcoded reasons)
- Configurable Layer 3 prompt text
- Conversation-level feedback (collapsible strip above input)
- Admin UI to enable/disable layers, manage tags, set scales
- Backend config + public API for feedback settings

### Key Discoveries:
- `RateComment.svelte:16-33` — hardcoded reason arrays with i18n mapping via if/else chain (lines 178-208)
- `RateComment.svelte:135-148` — hardcoded 1-10 scale, constrained by thumbs direction
- `config.py:1573-1582` — `PersistentConfig` pattern: `(name, config_path, default_value)`
- `evaluations.py:27-59` — GET/POST `/config` endpoints for arena config (admin-only)
- `main.py:2005` — config exposed to frontend via `features.enable_message_rating`
- `Chat.svelte:2508-2510` — gap between messages container and input wrapper where conversation feedback goes
- `Chat.svelte:2473` — `createMessagesList(history, history.currentId).length > 0` for message count
- `feedback` table uses JSON columns with `extra="allow"` — no migration needed for new fields
- i18n returns the key itself when no translation exists — custom tag labels display as-is

## Desired End State

### Per-Turn Feedback (configurable via admin):
- **Layer 1**: Thumbs up/down (existing, toggleable)
- **Layer 2**: Admin-defined issue tags shown after thumbs rating, replacing hardcoded reasons. Tags stored as `{ key: string, label: string }` with optional multi-language labels. Multiple tags selectable.
- **Layer 3**: Free text with admin-configurable prompt (e.g., "Wat had het antwoord moeten zijn?"), optional

### Conversation-Level Feedback (new):
- Collapsible strip above the input box, visible after 2+ messages
- Collapsed: thin divider-like element "Hoe was dit gesprek?" / "How was this conversation?"
- Expanded: configurable numeric scale (e.g., 1-5) + optional free text field
- Stored in the `feedback` table with `meta.scope: "conversation"` and `meta.chat_id`

### Admin Configuration (Settings > Evaluations):
- New "Feedback" section below the existing "Arena Models" section
- Toggle enable/disable for each layer independently
- Layer 2: add/edit/delete custom tags with label field
- Layer 3: configurable prompt text
- Conversation feedback: toggle + scale configuration (min/max values)

### Verification:
- Admin can toggle each feedback layer on/off and changes reflect immediately in chat UI
- Custom tags appear in the RateComment panel when Layer 2 is enabled
- Conversation feedback strip appears after 2+ messages when enabled
- All feedback data (per-turn and conversation) is visible in the admin Feedbacks list
- Feedback export includes conversation-level feedback
- `npm run build` succeeds

## What We're NOT Doing

- No neutral rating option (confirmed: thumbs up/down only)
- No changes to the 1-10 detailed rating scale in RateComment (keeping as-is)
- No changes to the Leaderboard or Elo calculation
- No changes to auto-generated tags via LLM
- No database migration — the JSON columns handle new fields
- No i18n translation file changes for custom tag labels (rendered as raw strings)

## Implementation Approach

Three phases, each independently testable:
1. **Backend config** — Add PersistentConfig values and API endpoints for feedback settings
2. **Frontend config-driven feedback** — Make RateComment and ResponseMessage read from config, add conversation feedback component
3. **Admin UI** — Build the feedback configuration section in Settings/Evaluations

---

## Phase 1: Backend — Feedback Configuration

### Overview
Add PersistentConfig values for all feedback layer settings and expose them via the existing evaluations config endpoints. Also expose feedback config to non-admin users via the main `/api/config` response.

### Changes Required:

#### 1. Add PersistentConfig values
**File**: `backend/open_webui/config.py`
**Location**: After line 1582 (after `EVALUATION_ARENA_MODELS`)

```python
####################################
# Feedback Configuration
####################################

ENABLE_FEEDBACK_LAYER1 = PersistentConfig(
    "ENABLE_FEEDBACK_LAYER1",
    "evaluation.feedback.layer1.enable",
    os.environ.get("ENABLE_FEEDBACK_LAYER1", "True").lower() == "true",
)

ENABLE_FEEDBACK_LAYER2 = PersistentConfig(
    "ENABLE_FEEDBACK_LAYER2",
    "evaluation.feedback.layer2.enable",
    os.environ.get("ENABLE_FEEDBACK_LAYER2", "True").lower() == "true",
)

FEEDBACK_LAYER2_TAGS = PersistentConfig(
    "FEEDBACK_LAYER2_TAGS",
    "evaluation.feedback.layer2.tags",
    [],  # Default: empty list → falls back to hardcoded reasons in frontend
)

ENABLE_FEEDBACK_LAYER3 = PersistentConfig(
    "ENABLE_FEEDBACK_LAYER3",
    "evaluation.feedback.layer3.enable",
    os.environ.get("ENABLE_FEEDBACK_LAYER3", "True").lower() == "true",
)

FEEDBACK_LAYER3_PROMPT = PersistentConfig(
    "FEEDBACK_LAYER3_PROMPT",
    "evaluation.feedback.layer3.prompt",
    "",  # Empty string → use default placeholder
)

ENABLE_CONVERSATION_FEEDBACK = PersistentConfig(
    "ENABLE_CONVERSATION_FEEDBACK",
    "evaluation.feedback.conversation.enable",
    os.environ.get("ENABLE_CONVERSATION_FEEDBACK", "False").lower() == "true",
)

CONVERSATION_FEEDBACK_SCALE_MAX = PersistentConfig(
    "CONVERSATION_FEEDBACK_SCALE_MAX",
    "evaluation.feedback.conversation.scale_max",
    int(os.environ.get("CONVERSATION_FEEDBACK_SCALE_MAX", "5")),
)

CONVERSATION_FEEDBACK_PROMPT = PersistentConfig(
    "CONVERSATION_FEEDBACK_PROMPT",
    "evaluation.feedback.conversation.prompt",
    "",  # Empty → use default
)
```

#### 2. Extend evaluations config endpoints
**File**: `backend/open_webui/routers/evaluations.py`

Add feedback config fields to the existing `get_config` and `update_config` endpoints:

```python
# In get_config (line 29), add to the return dict:
"ENABLE_FEEDBACK_LAYER1": request.app.state.config.ENABLE_FEEDBACK_LAYER1,
"ENABLE_FEEDBACK_LAYER2": request.app.state.config.ENABLE_FEEDBACK_LAYER2,
"FEEDBACK_LAYER2_TAGS": request.app.state.config.FEEDBACK_LAYER2_TAGS,
"ENABLE_FEEDBACK_LAYER3": request.app.state.config.ENABLE_FEEDBACK_LAYER3,
"FEEDBACK_LAYER3_PROMPT": request.app.state.config.FEEDBACK_LAYER3_PROMPT,
"ENABLE_CONVERSATION_FEEDBACK": request.app.state.config.ENABLE_CONVERSATION_FEEDBACK,
"CONVERSATION_FEEDBACK_SCALE_MAX": request.app.state.config.CONVERSATION_FEEDBACK_SCALE_MAX,
"CONVERSATION_FEEDBACK_PROMPT": request.app.state.config.CONVERSATION_FEEDBACK_PROMPT,

# In UpdateConfigForm (line 40), add fields:
ENABLE_FEEDBACK_LAYER1: Optional[bool] = None
ENABLE_FEEDBACK_LAYER2: Optional[bool] = None
FEEDBACK_LAYER2_TAGS: Optional[list[dict]] = None
ENABLE_FEEDBACK_LAYER3: Optional[bool] = None
FEEDBACK_LAYER3_PROMPT: Optional[str] = None
ENABLE_CONVERSATION_FEEDBACK: Optional[bool] = None
CONVERSATION_FEEDBACK_SCALE_MAX: Optional[int] = None
CONVERSATION_FEEDBACK_PROMPT: Optional[str] = None

# In update_config (line 46), add handlers for each field following the existing pattern
```

#### 3. Expose feedback config to all users
**File**: `backend/open_webui/main.py`

Add feedback config to the features dict in the `get_app_config` endpoint (around line 2005):

```python
"enable_feedback_layer1": app.state.config.ENABLE_FEEDBACK_LAYER1,
"enable_feedback_layer2": app.state.config.ENABLE_FEEDBACK_LAYER2,
"feedback_layer2_tags": app.state.config.FEEDBACK_LAYER2_TAGS,
"enable_feedback_layer3": app.state.config.ENABLE_FEEDBACK_LAYER3,
"feedback_layer3_prompt": app.state.config.FEEDBACK_LAYER3_PROMPT,
"enable_conversation_feedback": app.state.config.ENABLE_CONVERSATION_FEEDBACK,
"conversation_feedback_scale_max": app.state.config.CONVERSATION_FEEDBACK_SCALE_MAX,
"conversation_feedback_prompt": app.state.config.CONVERSATION_FEEDBACK_PROMPT,
```

### Success Criteria:

#### Automated Verification:
- [ ] Backend starts without errors: `open-webui dev`
- [ ] `GET /api/v1/evaluations/config` returns all new fields (requires admin token)
- [ ] `POST /api/v1/evaluations/config` updates all new fields
- [ ] `GET /api/config` includes feedback config in `features` for non-admin users
- [x] `npm run build` succeeds

#### Manual Verification:
- [ ] Config values persist across server restarts (PersistentConfig)
- [ ] Default values are sensible (layers 1-3 enabled, conversation feedback disabled, empty tags)

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation before proceeding.

---

## Phase 2: Frontend — Config-Driven Feedback Components

### Overview
Make the existing per-turn feedback read from the config store, and add a new conversation-level feedback component (collapsible strip).

### Changes Required:

#### 1. Update RateComment to use config-driven tags
**File**: `src/lib/components/chat/Messages/RateComment.svelte`

**Replace** the hardcoded `LIKE_REASONS`/`DISLIKE_REASONS` arrays (lines 16-34) and the if/else label chain (lines 178-208) with config-driven tags:

```svelte
<script lang="ts">
    // ... existing imports ...
    import { config } from '$lib/stores';

    // Remove LIKE_REASONS and DISLIKE_REASONS arrays (lines 16-34)

    // Replace the reasons reactive block (lines 45-49) with:
    $: {
        const configTags = $config?.features?.feedback_layer2_tags ?? [];
        if (configTags.length > 0) {
            // Use admin-configured tags (same for both thumbs up/down)
            reasons = configTags.map(t => t.key);
            reasonLabels = configTags.reduce((acc, t) => {
                acc[t.key] = t.label;
                return acc;
            }, {});
        } else {
            // Fallback to hardcoded defaults
            const LIKE_REASONS = ['accurate_information', 'followed_instructions_perfectly', ...];
            const DISLIKE_REASONS = ['dont_like_the_style', 'too_verbose', ...];
            if (message?.annotation?.rating === 1) {
                reasons = LIKE_REASONS;
            } else if (message?.annotation?.rating === -1) {
                reasons = DISLIKE_REASONS;
            }
            reasonLabels = {}; // Empty = use i18n fallback
        }
    }

    // Check if layers are enabled
    $: layer2Enabled = $config?.features?.enable_feedback_layer2 ?? true;
    $: layer3Enabled = $config?.features?.enable_feedback_layer3 ?? true;
    $: layer3Prompt = $config?.features?.feedback_layer3_prompt || '';
</script>

<!-- In the template, replace the if/else chain for reason labels (lines 178-208): -->
{#each reasons as reason}
    <button ...>
        {reasonLabels[reason] ?? $i18n.t(reason.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()))}
    </button>
{/each}

<!-- Wrap the reasons section with layer2 check: -->
{#if layer2Enabled && reasons.length > 0}
    <!-- existing reasons UI -->
{/if}

<!-- Wrap the textarea with layer3 check and use configurable prompt: -->
{#if layer3Enabled}
    <textarea
        placeholder={layer3Prompt || $i18n.t('Feel free to add specific details')}
        ...
    />
{/if}
```

#### 2. Conditionally hide thumbs buttons when Layer 1 disabled
**File**: `src/lib/components/chat/Messages/ResponseMessage.svelte`
**Location**: Line 1208

Add Layer 1 check to the existing condition:

```svelte
{#if !$temporaryChatEnabled && ($config?.features.enable_message_rating ?? true) && ($config?.features?.enable_feedback_layer1 ?? true) && ($user?.role === 'admin' || ($user?.permissions?.chat?.rate_response ?? true))}
```

#### 3. Create ConversationFeedback component
**File**: `src/lib/components/chat/ConversationFeedback.svelte` (new)

Collapsible strip design (Option C):

```svelte
<script lang="ts">
    import { getContext } from 'svelte';
    import { config } from '$lib/stores';
    import { createNewFeedback, updateFeedbackById } from '$lib/apis/evaluations';
    import { toast } from 'svelte-sonner';
    import { fade } from 'svelte/transition';

    const i18n = getContext('i18n');

    export let chatId: string;
    export let messageCount: number = 0;

    let expanded = false;
    let selectedRating: number | null = null;
    let comment = '';
    let feedbackId: string | null = null;
    let submitted = false;

    $: scaleMax = $config?.features?.conversation_feedback_scale_max ?? 5;
    $: prompt = $config?.features?.conversation_feedback_prompt || $i18n.t('Any thoughts on the overall conversation?');
    $: enabled = ($config?.features?.enable_conversation_feedback ?? false) && messageCount >= 2;

    const submitFeedback = async () => {
        if (!selectedRating) return;

        const feedbackItem = {
            type: 'rating',
            data: {
                rating: selectedRating,
                comment: comment || undefined,
            },
            meta: {
                scope: 'conversation',
                chat_id: chatId,
                scale_max: scaleMax,
            },
            snapshot: {}
        };

        try {
            if (feedbackId) {
                await updateFeedbackById(localStorage.token, feedbackId, feedbackItem);
            } else {
                const feedback = await createNewFeedback(localStorage.token, feedbackItem);
                if (feedback) feedbackId = feedback.id;
            }
            submitted = true;
            toast.success($i18n.t('Thanks for your feedback!'));
        } catch (error) {
            toast.error(`${error}`);
        }
    };
</script>

{#if enabled}
    <div class="w-full max-w-4xl mx-auto px-4" transition:fade={{ duration: 150 }}>
        {#if !expanded}
            <!-- Collapsed: thin clickable divider -->
            <button
                class="w-full flex items-center gap-3 py-1.5 text-xs text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-400 transition group"
                on:click={() => { expanded = true; }}
            >
                <div class="flex-1 h-px bg-gray-200 dark:bg-gray-700 group-hover:bg-gray-300 dark:group-hover:bg-gray-600 transition" />
                <span class="whitespace-nowrap">{submitted ? $i18n.t('Feedback submitted') : $i18n.t('How was this conversation?')}</span>
                <div class="flex-1 h-px bg-gray-200 dark:bg-gray-700 group-hover:bg-gray-300 dark:group-hover:bg-gray-600 transition" />
            </button>
        {:else}
            <!-- Expanded: rating + free text -->
            <div class="border border-gray-100/30 dark:border-gray-850/30 rounded-xl px-4 py-3 mb-1" transition:fade={{ duration: 150 }}>
                <div class="flex justify-between items-center mb-2">
                    <div class="text-sm font-medium">{$i18n.t('How was this conversation?')}</div>
                    <button class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300" on:click={() => { expanded = false; }}>
                        <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
                        </svg>
                    </button>
                </div>

                <div class="flex gap-1.5 mb-2">
                    {#each Array.from({ length: scaleMax }).map((_, i) => i + 1) as rating}
                        <button
                            class="size-8 text-sm border border-gray-100/30 dark:border-gray-850/30 hover:bg-gray-50 dark:hover:bg-gray-850 {selectedRating === rating ? 'bg-gray-100 dark:bg-gray-800' : ''} transition rounded-full"
                            on:click={() => { selectedRating = rating; }}
                        >
                            {rating}
                        </button>
                    {/each}
                </div>

                <textarea
                    bind:value={comment}
                    class="w-full text-sm px-1 py-2 bg-transparent outline-hidden resize-none rounded-xl"
                    placeholder={prompt}
                    rows="2"
                />

                <div class="flex justify-end mt-1">
                    <button
                        class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full disabled:opacity-50 disabled:cursor-not-allowed"
                        disabled={!selectedRating}
                        on:click={submitFeedback}
                    >
                        {$i18n.t('Submit')}
                    </button>
                </div>
            </div>
        {/if}
    </div>
{/if}
```

#### 4. Mount ConversationFeedback in Chat.svelte
**File**: `src/lib/components/chat/Chat.svelte`
**Location**: Between line 2508 (end of messages container) and line 2510 (start of input wrapper)

```svelte
<!-- Import at top of script -->
import ConversationFeedback from './ConversationFeedback.svelte';

<!-- Insert between messages container and input wrapper (after line 2508): -->
<ConversationFeedback
    chatId={$chatId}
    messageCount={createMessagesList(history, history.currentId).length}
/>
```

### Success Criteria:

#### Automated Verification:
- [x] `npm run build` succeeds
- [ ] `npm run check` doesn't introduce new errors beyond the pre-existing ~8000

#### Manual Verification:
- [ ] When Layer 1 is disabled in config, thumbs buttons are hidden
- [ ] When Layer 2 is disabled, reason tags don't appear in RateComment
- [ ] When Layer 2 has custom tags configured, they appear instead of hardcoded reasons
- [ ] When Layer 2 tags list is empty, hardcoded defaults still work
- [ ] When Layer 3 is disabled, comment textarea is hidden
- [ ] When Layer 3 has a custom prompt, it shows as the textarea placeholder
- [ ] Conversation feedback strip appears after 2+ messages (1 user + 1 assistant)
- [ ] Conversation feedback strip is hidden when disabled in config
- [ ] Clicking the strip expands to show scale + free text
- [ ] Submitting conversation feedback creates a record with `meta.scope: "conversation"`
- [ ] Can re-open and edit conversation feedback after submitting

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation before proceeding.

---

## Phase 3: Admin UI — Feedback Configuration

### Overview
Add a "Feedback Configuration" section to the Evaluations settings tab in the admin panel.

### Changes Required:

#### 1. Extend the Evaluations settings component
**File**: `src/lib/components/admin/Settings/Evaluations.svelte`

Add a new section after the Arena Models section (after line 166). The config values are already loaded/saved via `evaluationConfig`:

```svelte
<!-- After the Arena Models section (after line 166, before </div> at 167): -->

<!-- Feedback Configuration Section -->
<div class="mb-3">
    <div class="mt-0.5 mb-2.5 text-base font-medium">{$i18n.t('Feedback')}</div>
    <hr class="border-gray-100/30 dark:border-gray-850/30 my-2" />

    <!-- Layer 1: Thumbs Up/Down -->
    <div class="mb-2.5 flex w-full justify-between">
        <div class="flex flex-col">
            <div class="text-xs font-medium">{$i18n.t('Message Rating (Thumbs Up/Down)')}</div>
            <div class="text-xs text-gray-500">{$i18n.t('Allow users to rate individual responses')}</div>
        </div>
        <Switch bind:state={evaluationConfig.ENABLE_FEEDBACK_LAYER1} />
    </div>

    <!-- Layer 2: Issue Tags -->
    <div class="mb-2.5 flex w-full justify-between">
        <div class="flex flex-col">
            <div class="text-xs font-medium">{$i18n.t('Feedback Tags')}</div>
            <div class="text-xs text-gray-500">{$i18n.t('Custom tags shown after rating a response')}</div>
        </div>
        <Switch bind:state={evaluationConfig.ENABLE_FEEDBACK_LAYER2} />
    </div>

    {#if evaluationConfig.ENABLE_FEEDBACK_LAYER2}
        <div class="ml-2 mb-3">
            <div class="text-xs text-gray-500 mb-2">
                {$i18n.t('Leave empty to use default tags. Add custom tags below:')}
            </div>
            <!-- Tag list with add/edit/delete -->
            {#each evaluationConfig.FEEDBACK_LAYER2_TAGS ?? [] as tag, index}
                <div class="flex items-center gap-2 mb-1.5">
                    <input
                        class="flex-1 text-sm px-2.5 py-1 bg-transparent border border-gray-100/30 dark:border-gray-850/30 rounded-lg outline-hidden"
                        bind:value={tag.label}
                        placeholder={$i18n.t('Tag label')}
                    />
                    <button
                        type="button"
                        class="p-1 text-gray-400 hover:text-red-500 transition"
                        on:click={() => {
                            evaluationConfig.FEEDBACK_LAYER2_TAGS = evaluationConfig.FEEDBACK_LAYER2_TAGS.filter((_, i) => i !== index);
                        }}
                    >
                        <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
                        </svg>
                    </button>
                </div>
            {/each}
            <button
                type="button"
                class="text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 flex items-center gap-1 mt-1"
                on:click={() => {
                    if (!evaluationConfig.FEEDBACK_LAYER2_TAGS) evaluationConfig.FEEDBACK_LAYER2_TAGS = [];
                    const key = `tag_${Date.now()}`;
                    evaluationConfig.FEEDBACK_LAYER2_TAGS = [...evaluationConfig.FEEDBACK_LAYER2_TAGS, { key, label: '' }];
                }}
            >
                <Plus className="size-3" /> {$i18n.t('Add tag')}
            </button>
        </div>
    {/if}

    <!-- Layer 3: Free Text -->
    <div class="mb-2.5 flex w-full justify-between">
        <div class="flex flex-col">
            <div class="text-xs font-medium">{$i18n.t('Free Text Comment')}</div>
            <div class="text-xs text-gray-500">{$i18n.t('Allow users to leave a text comment on responses')}</div>
        </div>
        <Switch bind:state={evaluationConfig.ENABLE_FEEDBACK_LAYER3} />
    </div>

    {#if evaluationConfig.ENABLE_FEEDBACK_LAYER3}
        <div class="ml-2 mb-3">
            <div class="text-xs text-gray-500 mb-1">{$i18n.t('Custom prompt text (optional)')}</div>
            <input
                class="w-full text-sm px-2.5 py-1.5 bg-transparent border border-gray-100/30 dark:border-gray-850/30 rounded-lg outline-hidden"
                bind:value={evaluationConfig.FEEDBACK_LAYER3_PROMPT}
                placeholder={$i18n.t('Feel free to add specific details')}
            />
        </div>
    {/if}

    <hr class="border-gray-100/30 dark:border-gray-850/30 my-2" />

    <!-- Conversation-Level Feedback -->
    <div class="mb-2.5 flex w-full justify-between">
        <div class="flex flex-col">
            <div class="text-xs font-medium">{$i18n.t('Conversation Feedback')}</div>
            <div class="text-xs text-gray-500">{$i18n.t('Show a feedback strip above the input after 2+ messages')}</div>
        </div>
        <Switch bind:state={evaluationConfig.ENABLE_CONVERSATION_FEEDBACK} />
    </div>

    {#if evaluationConfig.ENABLE_CONVERSATION_FEEDBACK}
        <div class="ml-2 mb-3">
            <div class="flex items-center gap-2 mb-2">
                <div class="text-xs text-gray-500">{$i18n.t('Scale')}</div>
                <span class="text-xs">1 –</span>
                <input
                    type="number"
                    class="w-16 text-sm px-2 py-1 bg-transparent border border-gray-100/30 dark:border-gray-850/30 rounded-lg outline-hidden"
                    bind:value={evaluationConfig.CONVERSATION_FEEDBACK_SCALE_MAX}
                    min="2"
                    max="10"
                />
            </div>
            <div class="text-xs text-gray-500 mb-1">{$i18n.t('Custom prompt text (optional)')}</div>
            <input
                class="w-full text-sm px-2.5 py-1.5 bg-transparent border border-gray-100/30 dark:border-gray-850/30 rounded-lg outline-hidden"
                bind:value={evaluationConfig.CONVERSATION_FEEDBACK_PROMPT}
                placeholder={$i18n.t('Any thoughts on the overall conversation?')}
            />
        </div>
    {/if}
</div>
```

#### 2. Update the UpdateConfigForm handler
**File**: `src/lib/components/admin/Settings/Evaluations.svelte`

The existing `submitHandler` already sends the full `evaluationConfig` object via `updateConfig()`. Since we're binding new fields to `evaluationConfig`, they'll be included automatically. No changes needed to the save logic.

#### 3. Auto-generate tag keys from labels
In the tag input, auto-set `key` from `label` when label changes:

```svelte
<!-- In the tag input's on:input handler: -->
on:input={() => {
    tag.key = tag.label.toLowerCase().replace(/\s+/g, '_').replace(/[^a-z0-9_]/g, '');
}}
```

### Success Criteria:

#### Automated Verification:
- [x] `npm run build` succeeds
- [ ] `npm run check` doesn't introduce new errors beyond pre-existing

#### Manual Verification:
- [ ] Admin Settings > Evaluations shows the new "Feedback" section
- [ ] All toggles work and persist after saving
- [ ] Custom tags can be added, edited, and deleted
- [ ] Tag labels are free-form text (Dutch, English, anything)
- [ ] Scale max for conversation feedback can be changed (2-10)
- [ ] Custom prompts are saved and reflected in the chat UI
- [ ] Toggling Layer 1 off hides thumbs buttons in chat
- [ ] Toggling Layer 2 off hides reason tags in RateComment
- [ ] Toggling Layer 3 off hides the comment textarea
- [ ] Toggling Conversation Feedback on shows the strip in chat
- [ ] All settings persist across page refreshes

**Implementation Note**: After completing this phase and all verification passes, the feature is complete.

---

## Testing Strategy

### Unit Tests:
- Not required for this feature (config pass-through, no complex logic)

### Integration Tests:
- Backend: Verify `/evaluations/config` GET/POST roundtrip for all new fields
- Frontend: Verify `ConversationFeedback` component renders/hides correctly based on config

### Manual Testing Steps:
1. As admin, go to Settings > Evaluations and configure custom tags (e.g., "Verkeerde regeling", "Regeling gemist")
2. Disable Layer 3, save
3. Open a chat, send 2 messages, give a thumbs down on the response
4. Verify custom tags appear (not hardcoded English reasons), no comment textarea
5. Verify conversation feedback strip appears above input
6. Click the strip, rate 4/5, add a comment, submit
7. Go to admin Evaluations > Feedbacks, verify both per-turn and conversation feedback appear
8. Export feedbacks, verify conversation feedback has `meta.scope: "conversation"`

## Performance Considerations

- Feedback config is loaded once via `/api/config` on app init (already cached in the `$config` store) — no extra API calls per message
- ConversationFeedback component is lightweight, only renders when `messageCount >= 2`

## References

- Research: `thoughts/shared/research/2026-03-09-configurable-feedback-mechanism.md`
- Existing feedback handler: `src/lib/components/chat/Messages/ResponseMessage.svelte:418-534`
- Existing RateComment: `src/lib/components/chat/Messages/RateComment.svelte`
- Admin Evaluations settings: `src/lib/components/admin/Settings/Evaluations.svelte`
- Backend config: `backend/open_webui/config.py:1573-1582`
- Backend router: `backend/open_webui/routers/evaluations.py`
- Frontend API: `src/lib/apis/evaluations/index.ts`
- Chat layout: `src/lib/components/chat/Chat.svelte:2472-2546`
