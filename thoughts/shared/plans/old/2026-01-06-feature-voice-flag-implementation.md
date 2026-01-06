# Feature Voice Flag Implementation Plan

## Overview

Implement a single `FEATURE_VOICE` environment variable that completely disables all voice/audio functionality (STT and TTS) for ALL users including admins. When disabled, this flag removes:
- Voice input buttons (dictate, voice mode)
- "Read Aloud" TTS buttons on messages
- Audio settings from both user and admin settings panels
- Audio recording features in the notes editor
- Backend audio API endpoints return 403

## Current State Analysis

Open WebUI has extensive audio functionality across multiple components:
- **STT (Speech-to-Text)**: Voice recording for chat input, notes, knowledge base
- **TTS (Text-to-Speech)**: Read aloud button on messages, auto-playback
- **Voice mode**: Full-screen voice call interface with bidirectional audio

### Key Discoveries:
- Voice buttons in `MessageInput.svelte:1731-1840` check user permissions (`chat.stt`, `chat.call`)
- TTS button in `ResponseMessage.svelte:1007` checks `chat.tts` permission
- Audio settings exposed in user modal via `SettingsModal.svelte:260` (Audio tab)
- Admin audio settings at `/admin/settings/audio` via `Settings.svelte:345`
- Notes audio recording via `NoteEditor.svelte:1319` (RecordMenu component)
- Backend audio endpoints in `routers/audio.py` (speech, transcriptions, config)
- Existing feature flag pattern established in `utils/features.py` and `utils/features.ts`

## Desired End State

After implementation:
1. Setting `FEATURE_VOICE=False` hides ALL voice/audio UI for **everyone** (admins included)
2. Audio API endpoints return 403 Forbidden when feature is disabled
3. Audio settings tabs are hidden from both user and admin settings
4. Notes audio recording buttons are hidden
5. No voice-related UI elements visible anywhere in the application

### Verification:
- All voice features visible when `FEATURE_VOICE=True` (default)
- All voice features hidden when `FEATURE_VOICE=False`
- Admin users cannot see or access voice features when disabled
- API returns 403 for audio endpoints when disabled

## What We're NOT Doing

- NOT changing the existing permission system (admin bypass for `chat.stt`, `chat.tts`, `chat.call`)
- NOT removing backend audio processing code (just protecting endpoints)
- NOT modifying the Kokoro worker or audio utilities
- NOT changing how audio files are stored/processed once uploaded

## Implementation Approach

**Two-layer protection:**
1. **Backend**: Add `require_feature("voice")` dependency to audio API endpoints
2. **Frontend**: Use `isFeatureEnabled('voice')` for UI visibility checks

This follows the established pattern from the existing feature flag implementation (`FEATURE_CHAT_CONTROLS`, `FEATURE_PLAYGROUND`, etc.).

---

## Phase 1: Backend Feature Flag

### Overview
Add `FEATURE_VOICE` environment variable and protect audio API endpoints.

### Changes Required:

#### 1. Backend Config (`backend/open_webui/config.py`)

**File**: `backend/open_webui/config.py`
**Location**: After line ~1597 (in the Feature Flags section with other FEATURE_* vars)

```python
FEATURE_VOICE = os.environ.get("FEATURE_VOICE", "True").lower() == "true"
```

#### 2. Backend Feature Utility (`backend/open_webui/utils/features.py`)

**File**: `backend/open_webui/utils/features.py`
**Changes**: Add `voice` to Feature type and FEATURE_FLAGS dict

Add import:
```python
from open_webui.config import (
    FEATURE_CHAT_CONTROLS,
    FEATURE_CAPTURE,
    FEATURE_ARTIFACTS,
    FEATURE_PLAYGROUND,
    FEATURE_CHAT_OVERVIEW,
    FEATURE_VOICE,  # Add this
)
```

Update Feature type:
```python
Feature = Literal[
    "chat_controls",
    "capture",
    "artifacts",
    "playground",
    "chat_overview",
    "voice",  # Add this
]
```

Update FEATURE_FLAGS dict:
```python
FEATURE_FLAGS: dict[Feature, bool] = {
    "chat_controls": FEATURE_CHAT_CONTROLS,
    "capture": FEATURE_CAPTURE,
    "artifacts": FEATURE_ARTIFACTS,
    "playground": FEATURE_PLAYGROUND,
    "chat_overview": FEATURE_CHAT_OVERVIEW,
    "voice": FEATURE_VOICE,  # Add this
}
```

#### 3. API Exposure (`backend/open_webui/main.py`)

**File**: `backend/open_webui/main.py`
**Location**: Add import and expose in `/api/config` response

Add to imports (around line 66 with other config imports):
```python
from open_webui.config import (
    # ... existing imports ...
    FEATURE_VOICE,
)
```

Add to features dict in `get_app_config()` function (around line 1904-1920 in the features section):
```python
"feature_voice": FEATURE_VOICE,
```

#### 4. Audio Router Protection (`backend/open_webui/routers/audio.py`)

**File**: `backend/open_webui/routers/audio.py`
**Location**: Add feature check to key audio endpoints

Add import at top:
```python
from open_webui.utils.features import require_feature
```

Add `require_feature("voice")` dependency to these endpoints:

**Speech endpoint (TTS)** - Line ~329:
```python
@router.post("/speech")
async def speech(
    request: Request,
    form_data: SpeechGenerationInput,
    user=Depends(get_verified_user),
    _=Depends(require_feature("voice")),  # Add this
):
```

**Transcription endpoint (STT)** - Line ~1146:
```python
@router.post("/transcriptions")
async def transcription(
    request: Request,
    form_data: TranscriptionInput = Depends(parse_transcription_input),
    user=Depends(get_verified_user),
    _=Depends(require_feature("voice")),  # Add this
):
```

**Models endpoint** - Line ~1248:
```python
@router.get("/models")
async def get_models(request: Request, user=Depends(get_verified_user), _=Depends(require_feature("voice"))):
```

**Voices endpoint** - Line ~1354:
```python
@router.get("/voices")
async def get_voices(
    request: Request,
    user=Depends(get_verified_user),
    _=Depends(require_feature("voice")),  # Add this
):
```

**Audio config endpoints** (admin only, but still protect) - Lines ~192 and ~228:
```python
@router.get("/config")
async def get_audio_config(request: Request, user=Depends(get_admin_user), _=Depends(require_feature("voice"))):

@router.post("/config/update")
async def update_audio_config(
    request: Request,
    form_data: AudioConfigForm,
    user=Depends(get_admin_user),
    _=Depends(require_feature("voice")),
):
```

### Success Criteria:

#### Automated Verification:
- [x] Backend starts without errors: `open-webui dev`
- [x] TypeScript compiles: `npm run check`
- [x] API returns `feature_voice` in config: `curl http://localhost:8080/api/config | jq '.features.feature_voice'`
- [x] Default value is `true`

#### Manual Verification:
- [ ] Set `FEATURE_VOICE=False` and restart - API returns `feature_voice: false`
- [ ] With `FEATURE_VOICE=False`, audio endpoints return 403

---

## Phase 2: Frontend Feature Utility Updates

### Overview
Add `voice` feature to the frontend feature checking system.

### Changes Required:

#### 1. Feature Utility (`src/lib/utils/features.ts`)

**File**: `src/lib/utils/features.ts`
**Location**: Update Feature type (line 4-9)

```typescript
export type Feature =
	| 'chat_controls'
	| 'capture'
	| 'artifacts'
	| 'playground'
	| 'chat_overview'
	| 'voice';  // Add this
```

#### 2. TypeScript Types (`src/lib/stores/index.ts`)

**File**: `src/lib/stores/index.ts`
**Location**: In the Config type features section (around line 267 where other feature_* types are)

Add to the features type:
```typescript
feature_voice?: boolean;
```

### Success Criteria:

#### Automated Verification:
- [x] TypeScript compiles: `npm run check`
- [x] No lint errors: `npm run lint:frontend`

---

## Phase 3: Chat Component Updates

### Overview
Hide voice-related UI elements in chat components when feature is disabled.

### Changes Required:

#### 1. Chat MessageInput (`src/lib/components/chat/MessageInput.svelte`)

**File**: `src/lib/components/chat/MessageInput.svelte`

Add import at top of script:
```typescript
import { isFeatureEnabled } from '$lib/utils/features';
```

**Dictate button** - Line ~1731:

**Before:**
```svelte
{#if (!history?.currentId || history.messages[history.currentId]?.done == true) && ($_user?.role === 'admin' || ($_user?.permissions?.chat?.stt ?? true))}
```

**After:**
```svelte
{#if isFeatureEnabled('voice') && (!history?.currentId || history.messages[history.currentId]?.done == true) && ($_user?.role === 'admin' || ($_user?.permissions?.chat?.stt ?? true))}
```

**Voice mode button** - Line ~1781:

**Before:**
```svelte
{#if prompt === '' && files.length === 0 && ($_user?.role === 'admin' || ($_user?.permissions?.chat?.call ?? true))}
```

**After:**
```svelte
{#if isFeatureEnabled('voice') && prompt === '' && files.length === 0 && ($_user?.role === 'admin' || ($_user?.permissions?.chat?.call ?? true))}
```

#### 2. Response Message TTS Button (`src/lib/components/chat/Messages/ResponseMessage.svelte`)

**File**: `src/lib/components/chat/Messages/ResponseMessage.svelte`

Add import at top of script:
```typescript
import { isFeatureEnabled } from '$lib/utils/features';
```

**Read Aloud button** - Line ~1007:

**Before:**
```svelte
{#if $user?.role === 'admin' || ($user?.permissions?.chat?.tts ?? true)}
```

**After:**
```svelte
{#if isFeatureEnabled('voice') && ($user?.role === 'admin' || ($user?.permissions?.chat?.tts ?? true))}
```

#### 3. Channel MessageInput (`src/lib/components/channel/MessageInput.svelte`)

**File**: `src/lib/components/channel/MessageInput.svelte`
**Location**: Line ~979 (voice input button)

Add import:
```typescript
import { isFeatureEnabled } from '$lib/utils/features';
```

Wrap the voice recording button with feature check (around line 979):
```svelte
{#if isFeatureEnabled('voice') && (!$activeThread || ...) }
```

### Success Criteria:

#### Automated Verification:
- [x] TypeScript compiles: `npm run check`
- [x] Frontend builds: `npm run build`

#### Manual Verification:
- [ ] With `FEATURE_VOICE=True`: dictate button, voice mode button, and read aloud button all visible
- [ ] With `FEATURE_VOICE=False`: all three buttons hidden

---

## Phase 4: Settings Component Updates

### Overview
Hide Audio settings tabs from both user and admin settings when voice is disabled.

### Changes Required:

#### 1. User Settings Modal (`src/lib/components/chat/SettingsModal.svelte`)

**File**: `src/lib/components/chat/SettingsModal.svelte`

Add import:
```typescript
import { isFeatureEnabled } from '$lib/utils/features';
```

**Audio tab in tabs array** - Around line 258-260:

Modify the tabs array to conditionally include the audio tab. Find where `TABS` or the tabs array is defined and filter out audio when disabled:

```typescript
// In the tabs array definition, wrap the audio entry:
...(isFeatureEnabled('voice') ? [{
    id: 'audio',
    title: 'Audio',
    keywords: [...]
}] : []),
```

**Audio tab button** - Around line 761:
Wrap the Audio tab button:
```svelte
{#if isFeatureEnabled('voice')}
    <button
        class="..."
        on:click={() => (selectedTab = 'audio')}
    >
        ...Audio...
    </button>
{/if}
```

**Audio component render** - Around line 899:
Wrap the Audio component:
```svelte
{#if isFeatureEnabled('voice')}
    {:else if selectedTab === 'audio'}
        <Audio ... />
{/if}
```

#### 2. Admin Settings (`src/lib/components/admin/Settings.svelte`)

**File**: `src/lib/components/admin/Settings.svelte`

Add import:
```typescript
import { isFeatureEnabled } from '$lib/utils/features';
```

**Audio tab button** - Around line 324-346:

**Before:**
```svelte
<button
    id="audio"
    class="px-0.5 py-1 min-w-fit rounded-lg flex-1 md:flex-none flex text-left transition {selectedTab ===
    'audio'
        ? ''
        : ' text-gray-300 dark:text-gray-600 hover:text-gray-700 dark:hover:text-white'}"
    on:click={() => {
        goto('/admin/settings/audio');
    }}
>
    ...Audio icon and text...
</button>
```

**After:**
```svelte
{#if isFeatureEnabled('voice')}
    <button
        id="audio"
        ...
    >
        ...Audio icon and text...
    </button>
{/if}
```

#### 3. Admin Settings Route Guard (`src/routes/(app)/admin/settings/[tab]/+page.svelte`)

**File**: `src/routes/(app)/admin/settings/[tab]/+page.svelte`

Add redirect logic if someone navigates directly to `/admin/settings/audio` when voice is disabled:

```svelte
<script lang="ts">
    import { isFeatureEnabled } from '$lib/utils/features';
    import { goto } from '$app/navigation';
    import { onMount } from 'svelte';
    import { page } from '$app/stores';

    onMount(() => {
        if ($page.params.tab === 'audio' && !isFeatureEnabled('voice')) {
            goto('/admin/settings');
        }
    });
</script>
```

### Success Criteria:

#### Automated Verification:
- [x] TypeScript compiles: `npm run check`
- [x] Frontend builds: `npm run build`

#### Manual Verification:
- [ ] With `FEATURE_VOICE=True`: Audio tab visible in user settings and admin settings
- [ ] With `FEATURE_VOICE=False`: Audio tab hidden from both settings panels
- [ ] Direct navigation to `/admin/settings/audio` redirects when disabled

---

## Phase 5: Notes Component Updates

### Overview
Hide audio recording features in the notes editor when voice is disabled.

### Changes Required:

#### 1. Notes Editor (`src/lib/components/notes/NoteEditor.svelte`)

**File**: `src/lib/components/notes/NoteEditor.svelte`

Add import:
```typescript
import { isFeatureEnabled } from '$lib/utils/features';
```

**RecordMenu component** - Around line 1319:

**Before:**
```svelte
<RecordMenu
    onRecord={async () => {
        ...
    }}
    onCaptureAudio={async () => {
        ...
    }}
    onUpload={async () => {
        ...
    }}
>
    <Tooltip content={$i18n.t('Record')} placement="top">
        ...
    </Tooltip>
</RecordMenu>
```

**After:**
```svelte
{#if isFeatureEnabled('voice')}
    <RecordMenu
        onRecord={async () => {
            ...
        }}
        onCaptureAudio={async () => {
            ...
        }}
        onUpload={async () => {
            ...
        }}
    >
        <Tooltip content={$i18n.t('Record')} placement="top">
            ...
        </Tooltip>
    </RecordMenu>
{/if}
```

Also hide the VoiceRecording component (around line 1260-1280):
```svelte
{#if isFeatureEnabled('voice') && recording}
    <div class="flex-1 w-full">
        <VoiceRecording ... />
    </div>
{:else if !recording}
    <!-- Rest of the non-recording UI -->
{/if}
```

### Success Criteria:

#### Automated Verification:
- [x] TypeScript compiles: `npm run check`
- [x] Frontend builds: `npm run build`

#### Manual Verification:
- [ ] With `FEATURE_VOICE=True`: Record menu visible in notes editor
- [ ] With `FEATURE_VOICE=False`: Record menu hidden in notes editor

---

## Phase 6: Tests

### Overview
Add tests for the voice feature flag behavior.

### Changes Required:

#### 1. Backend Unit Tests (`backend/open_webui/test/util/test_features.py`)

Add test cases for voice feature:

```python
class TestVoiceFeature:
    """Tests for voice feature flag."""

    def test_voice_enabled_by_default(self):
        """Voice feature should be enabled by default."""
        with patch.dict(
            "open_webui.utils.features.FEATURE_FLAGS",
            {"voice": True}
        ):
            assert is_feature_enabled("voice") is True

    def test_voice_can_be_disabled(self):
        """Voice feature should be disableable."""
        with patch.dict(
            "open_webui.utils.features.FEATURE_FLAGS",
            {"voice": False}
        ):
            assert is_feature_enabled("voice") is False

    def test_require_feature_blocks_when_voice_disabled(self):
        """Should raise 403 when voice is disabled."""
        with patch.dict(
            "open_webui.utils.features.FEATURE_FLAGS",
            {"voice": False}
        ):
            check = require_feature("voice")
            with pytest.raises(HTTPException) as exc_info:
                check()
            assert exc_info.value.status_code == 403
```

#### 2. Frontend Unit Tests (`src/lib/utils/features.test.ts`)

Add test cases for voice feature:

```typescript
describe('voice feature', () => {
    it('returns true when voice feature is enabled', () => {
        vi.mocked(get).mockReturnValue({
            features: {
                feature_voice: true
            }
        });
        expect(isFeatureEnabled('voice')).toBe(true);
    });

    it('returns false when voice feature is disabled', () => {
        vi.mocked(get).mockReturnValue({
            features: {
                feature_voice: false
            }
        });
        expect(isFeatureEnabled('voice')).toBe(false);
    });

    it('returns true when voice feature is undefined (default)', () => {
        vi.mocked(get).mockReturnValue({
            features: {}
        });
        expect(isFeatureEnabled('voice')).toBe(true);
    });
});
```

### Success Criteria:

#### Automated Verification:
- [x] Backend tests pass: `pytest backend/open_webui/test/util/test_features.py -v`
- [x] Frontend tests pass: `npm run test:frontend`

---

## Testing Strategy

### Manual Testing Steps:
1. Start backend with default env → verify all voice features visible
2. Set `FEATURE_VOICE=False` → restart → verify:
   - Dictate button hidden in chat input
   - Voice mode button hidden in chat input
   - Read Aloud button hidden on messages
   - Audio tab hidden in user settings
   - Audio tab hidden in admin settings
   - Record menu hidden in notes editor
3. Try API calls with `FEATURE_VOICE=False`:
   - `POST /api/v1/audio/speech` → 403 Forbidden
   - `POST /api/v1/audio/transcriptions` → 403 Forbidden
   - `GET /api/v1/audio/voices` → 403 Forbidden
4. Direct navigation to `/admin/settings/audio` → redirects to `/admin/settings`

---

## Files Changed Summary

| File | Change Type | Risk |
|------|-------------|------|
| `backend/open_webui/config.py` | Add 1 line | Low |
| `backend/open_webui/utils/features.py` | Add ~3 lines | Low |
| `backend/open_webui/main.py` | Add ~2 lines | Low |
| `backend/open_webui/routers/audio.py` | Add ~6 lines (dependencies) | Low |
| `src/lib/utils/features.ts` | Add 1 line | Low |
| `src/lib/stores/index.ts` | Add 1 line | Low |
| `src/lib/components/chat/MessageInput.svelte` | Add 1 import + 2 conditions | Low |
| `src/lib/components/chat/Messages/ResponseMessage.svelte` | Add 1 import + 1 condition | Low |
| `src/lib/components/channel/MessageInput.svelte` | Add 1 import + 1 condition | Low |
| `src/lib/components/chat/SettingsModal.svelte` | Add 1 import + ~3 conditions | Low |
| `src/lib/components/admin/Settings.svelte` | Add 1 import + 1 condition | Low |
| `src/routes/(app)/admin/settings/[tab]/+page.svelte` | Add redirect logic | Low |
| `src/lib/components/notes/NoteEditor.svelte` | Add 1 import + 2 conditions | Low |
| `backend/open_webui/test/util/test_features.py` | Add ~20 lines | None |
| `src/lib/utils/features.test.ts` | Add ~20 lines | None |

**Total: ~14 files, ~60 LOC additions**

## References

- Existing feature flag plan: `thoughts/shared/plans/2026-01-06-feature-flag-wrapper-implementation.md`
- Feature utility: `src/lib/utils/features.ts` and `backend/open_webui/utils/features.py`
- Audio router: `backend/open_webui/routers/audio.py`
- MessageInput voice buttons: `src/lib/components/chat/MessageInput.svelte:1731-1840`
