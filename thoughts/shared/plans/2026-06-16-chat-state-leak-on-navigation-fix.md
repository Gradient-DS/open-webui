# Chat State Leak on Navigation — Fix Implementation Plan

## Overview

Switching between chats (especially after toggling agent / web-search a few
times) corrupts chat state: a chat's URL/UUID is correct, but it displays the
content of a *different* conversation — typically the last agent web-search
chat — and several chats end up showing the same stale "stopped" conversation.
The original content is overwritten in the database, not just mis-rendered.

Root cause (verified by data-flow trace): a Gradient-custom feature-autosave
reactive in `Chat.svelte` persists the **previous** chat's `history` under the
**new** chat's id during the navigation/load window, because it keys off the
`$chatId` store (already updated to the new chat) while reading the component's
`history` variable (still holding the old chat's messages).

This plan implements two layers:
1. **The fix** — guard the autosave reactive so it cannot fire mid-navigation.
2. **Hardening** — make `loadChat` abort if the user navigates away mid-load,
   so a slow `getChatById` can't clobber a newer chat's state either.

## Current State Analysis

### The corruption vector (Phase 1 target)

`src/lib/components/chat/Chat.svelte:306-318` — a reactive added 2026-05-17
(commit `c1204faa8c "fix: bugs from staging"`) that persists feature toggles
(web search / image gen / code interpreter / document writer) to the chat:

```js
let lastSavedFeatures = '';
$: if ($chatId && !$temporaryChatEnabled && history?.currentId) {   // ⚠️ no !loading guard
	const current = JSON.stringify({
		webSearchEnabled,
		imageGenerationEnabled,
		codeInterpreterEnabled,
		documentWriterEnabled
	});
	if (current !== lastSavedFeatures) {
		lastSavedFeatures = current;
		saveChatHandler($chatId, history);   // → updateChatById($chatId, { history, messages, models, ... })
	}
}
```

`saveChatHandler` (`Chat.svelte:3172-3190`) writes the full `history` +
`messages` to whatever id it is passed:

```js
const saveChatHandler = async (_chatId, history) => {
	if ($chatId == _chatId) {                  // weak guard: caller passes $chatId, so always true
		if (!$temporaryChatEnabled) {
			chat = await updateChatById(localStorage.token, _chatId, {
				models: selectedModels,
				history: history,
				messages: createMessagesList(history, history.currentId),
				...
			});
		}
	}
};
```

### Why it corrupts — the navigation trace (A = Daan-Witte web-search chat → B = appelpie chat)

1. `navigateHandler` (`Chat.svelte:217`) runs on the `$: if (chatIdProp) navigateHandler()`
   reactive (`:207-209`). It resets `prompt`, `files`, and crucially
   `webSearchEnabled = false; imageGenerationEnabled = false` (`:234-235`) — but
   **does NOT reset `history`** (nor `selectedModels`, `codeInterpreterEnabled`,
   `documentWriterEnabled`). So `history` still holds A's messages.
2. It sets `loading = true` (`:226`) and calls `await loadChat()` (`:243`).
3. `loadChat` (`:1529`) runs `chatId.set(chatIdProp)` **first, before loading any
   content** → `$chatId` is now B.
4. `loadChat` hits `await getChatById(...)` (`:1535`) — a network fetch. The
   `await` yields; the Svelte scheduler flushes pending reactives (the `chatId`
   store `.set` scheduled a microtask flush) **before the fetch resolves**.
5. At that flush: `$chatId = B`, but `history` = **A's messages**, and
   `webSearchEnabled` just flipped `true → false`. So `current !== lastSavedFeatures`
   → reactive calls `saveChatHandler(B, A's history)` →
   `updateChatById(B, { history: A's, messages: A's, ... })`.
   **A's stopped web-search conversation is now written into chat B's DB record.**

The sibling reactive `saveControls` (`:212-215`) *is* guarded with `!loading`;
this newer one is not. That asymmetry is the bug.

### Why it matches every observed symptom

- **Wrong content on a correct URL/UUID**: B's DB record now contains A's
  `history`/`messages`; navigating to or reloading B shows A's content.
- **Only with agents / web search**: the save only fires when
  `current !== lastSavedFeatures`, and the only flags `navigateHandler` resets
  are `webSearchEnabled`/`imageGenerationEnabled`. So corruption fires
  specifically when leaving a chat that had **web search or image gen on** —
  i.e. the agent chats. Plain chats (all flags already false) yield
  `current == lastSavedFeatures` → no save.
- **Several chats show the *same* stopped chat**: every chat navigated *to*
  while `history` still holds A's content gets overwritten with it.
- **Original content "lost"**: it's a real `updateChatById` DB overwrite, not a
  render glitch — it survives reload. Open WebUI keeps no per-chat version
  history, so already-corrupted chats are not recoverable from the app.

### Secondary race (Phase 2 target)

`loadChat` (`:1528-1657`) has three `await` points (`:1535` getChatById,
`:1541` getTagsById, `:1634` getTaskIdsByChatId) and **never re-checks** that
the chat being loaded is still the current one. If chat A's `getChatById`
resolves *after* the user has navigated to chat C, `loadChat(A)` overwrites
`history`/`selectedModels`/`params`/`chatFiles` with A's content while `$chatId`
is C — the same class of leak, via a different trigger. `navigateHandler` does
not reset `history`/`selectedModels`/`params`/`chatFiles`, so there's nothing
neutral in the gap.

### Constraints discovered

- `loadChat` has exactly **one caller** (`navigateHandler:243`), so changing its
  return contract is safe.
- No component-test harness exists: devDeps include only `vitest` (no jsdom,
  no `@testing-library/svelte`); every existing `*.test.ts` is a pure-function
  test. Mounting `Chat.svelte` in a test is not the established pattern.
- The feature-autosave reactive is **Gradient-custom code**; the upstream
  `chatId.set(chatIdProp)` at the top of `loadChat` (2024) is safe upstream
  precisely because upstream has no such reactive. The Phase 1 change is
  entirely in custom code (zero upstream merge impact); Phase 2 touches the
  upstream `loadChat`/`navigateHandler` additively.

## Desired End State

Navigating between chats — in any order, while toggling agent/web-search, and
under rapid switching — never writes one chat's `history`/`messages` into
another chat's record. Each chat's content stays intact across navigation and
reload. The intended behaviour of the autosave reactive (persisting feature
toggles the user changes *during* a chat) is preserved.

Verify by: reproduce the original bug on the current code, apply the fix, and
confirm the repro no longer corrupts any chat (see Testing Strategy).

### Key Discoveries

- Corruption vector: `Chat.svelte:306-318` (unguarded feature-autosave reactive) — `c1204faa8c`, 2026-05-17.
- Save sink: `saveChatHandler` `Chat.svelte:3172-3190` writes full history/messages.
- The window opener: `chatId.set(chatIdProp)` at `Chat.svelte:1529`, before content load.
- `navigateHandler` resets at `Chat.svelte:228-237` omit `history`, `selectedModels`, `codeInterpreterEnabled`, `documentWriterEnabled`.
- Working reference: the `saveControls` reactive at `Chat.svelte:212` already uses the `!loading` guard pattern.

## What We're NOT Doing

- **Not** repairing already-corrupted chats in the DB — their original content
  is overwritten and unrecoverable from the app side. (If DB backups exist,
  recovery is a separate operational task. Flagged in Migration Notes.)
- **Not** adding a component-test harness (jsdom / `@testing-library/svelte`) —
  per decision, verification is manual repro + existing automated checks.
- **Not** moving `chatId.set()` to after the content load, nor reworking the
  socket `chatEventHandler` — keeping the change minimal and upstream-merge-safe.
- **Not** hardening the `saveControls` params/files autosave against rapid
  navigation. It is already `!loading`-guarded and only writes `params`/`files`
  (not message history), so it cannot reproduce the reported corruption. Noted
  as a known lower-severity edge case, out of scope here.
- **Not** changing agent routing / `pendingAgentId` behaviour — that is sticky
  by design and is not the cause of this corruption.

## Implementation Approach

Phase 1 is the fix for the reported bug and is independently shippable. Phase 2
is defensive hardening that closes the same class of leak via the slow-load
race. Do them as separate commits so each can be reviewed and verified on its
own.

---

## Phase 1: Guard the feature-autosave reactive

### Overview
Prevent the feature-autosave reactive from firing during the navigation/load
window, and keep its baseline (`lastSavedFeatures`) in sync with the chat just
loaded so it doesn't emit a redundant full-history save on every navigation.

### Changes Required:

#### 1. Add the `!loading` guard
**File**: `src/lib/components/chat/Chat.svelte` (~line 307)
**Changes**: mirror the `saveControls` reactive's `!loading` guard.

```js
// before
$: if ($chatId && !$temporaryChatEnabled && history?.currentId) {

// after
$: if ($chatId && !loading && !$temporaryChatEnabled && history?.currentId) {
```

During `navigateHandler`, `loading` is `true` from `:226` until `:245`, which
covers the entire `loadChat` await window — so the reactive can no longer fire
with a mismatched `($chatId, history)` pair. It still fires normally for
genuine user feature toggles during a loaded chat (`loading === false`).

#### 2. Sync `lastSavedFeatures` after load
**File**: `src/lib/components/chat/Chat.svelte` (immediately after `:1597`, where
`documentWriterEnabled` is set from the loaded chat)
**Changes**: set the baseline to the loaded chat's features so the
post-load flush (when `loading` flips to `false`) sees `current === lastSavedFeatures`
and does not emit a redundant `saveChatHandler(B, B's history)` PUT on every
navigation.

```js
documentWriterEnabled = chatFeatures.document_writer ?? false;

// [Gradient] Keep the feature-autosave baseline in sync with the chat we just
// loaded, so the reactive at the feature-persist block does not emit a
// redundant full-history save right after load. Key order must match that block.
lastSavedFeatures = JSON.stringify({
	webSearchEnabled,
	imageGenerationEnabled,
	codeInterpreterEnabled,
	documentWriterEnabled
});
```

### Success Criteria:

#### Automated Verification:
- [x] Type-check passes: `npm run check` — 0 new errors in Chat.svelte (338 baseline = 338 with change; repo has pre-existing errors in unrelated files)
- [x] Frontend lint passes: `npm run lint:frontend` — 0 new errors in Chat.svelte (47 baseline = 47 with change; full-repo lint crashes pre-existing on FilePreview.svelte, unrelated)
- [x] Existing unit tests pass (no regression): `npm run test:frontend` — 131 passed, 10 skipped, 0 failed

#### Manual Verification:
- [ ] Repro on a build *without* the fix first to confirm the steps corrupt a chat (baseline).
- [ ] With the fix: create/open an agent chat A that performs a web search; navigate A → chat B; reload B → B still shows B's own content (not A's).
- [ ] Toggle agent/no-agent and switch among several chats repeatedly → no chat shows another chat's content; no chat's content is lost.
- [ ] Toggling web search / image gen *within* a single loaded chat still persists across reload (the reactive's intended behaviour is intact).
- [ ] Temporary chats are unaffected (no saves; `$temporaryChatEnabled` path).

**Implementation Note**: After automated checks pass, pause for manual
confirmation that the repro is fixed before proceeding to Phase 2.

---

## Phase 2: Harden `loadChat` against navigation races

### Overview
Make `loadChat` capture the chat id it started loading and abort (without
touching component state or redirecting) if the user navigates away before a
fetch resolves. This closes the slow-`getChatById`-resolves-after-navigation
leak for `history`/`selectedModels`/`params`/`chatFiles`.

### Changes Required:

#### 1. Abort-aware `loadChat`
**File**: `src/lib/components/chat/Chat.svelte:1528-1657`
**Changes**: capture `targetId`, use it for fetches, re-check after each await,
and return a status string instead of a boolean/null. Move the not-found
redirect out of the `getChatById` catch into the single caller.

```js
const loadChat = async () => {
	const targetId = chatIdProp;          // the chat this load is responsible for
	chatId.set(targetId);

	if ($temporaryChatEnabled) {
		temporaryChatEnabled.set(false);
	}

	chat = await getChatById(localStorage.token, targetId).catch(() => null);

	// A newer navigation took over while we were fetching — leave its state alone.
	if (chatIdProp !== targetId) return 'aborted';
	if (!chat) return 'not_found';

	tags = await getTagsById(localStorage.token, targetId).catch(() => []);
	if (chatIdProp !== targetId) return 'aborted';

	const chatContent = chat.chat;
	if (!chatContent) return 'not_found';

	// ... existing assignment block unchanged:
	//     selectedModels, history (+ sanitization), chatTitle,
	//     params, chatFiles, feature flags, lastSavedFeatures sync (Phase 1.2),
	//     chatTasks, autoScroll, mark-done loop, contextUsage rehydrate ...

	const pendingTaskIds = await getTaskIdsByChatId(localStorage.token, targetId)
		.then((res) => res?.task_ids ?? [])
		.catch(() => []);
	if (chatIdProp !== targetId) return 'aborted';

	// ... existing task reconciliation block unchanged ...

	await tick();
	return 'loaded';
};
```

Notes:
- Use `targetId` (not `$chatId`) for the three fetch calls so a concurrent
  store change can't redirect them.
- The `getChatById` catch no longer calls `goto('/')`; the caller owns redirect.

#### 2. Handle the new status in `navigateHandler`
**File**: `src/lib/components/chat/Chat.svelte:243-285`
**Changes**: branch on the status; on `'aborted'`, return early without
clearing `loading` or redirecting (the newer handler owns `loading`).

```js
const loadResult = chatIdProp ? await loadChat() : 'not_found';

if (loadResult === 'aborted') {
	return; // a newer navigateHandler is in charge; don't reset loading or redirect
}

if (loadResult === 'loaded') {
	await tick();
	loading = false;
	window.setTimeout(() => scrollToBottom(), 0);
	// ... rest of the existing success branch unchanged
	//     (updateLastReadAt, processNextInQueue, storageChatInput / setDefaults, focus) ...
} else {
	await goto('/');
}
```

### Success Criteria:

#### Automated Verification:
- [x] Type-check passes: `npm run check` — 0 new errors in Chat.svelte (338 = dev baseline)
- [x] Frontend lint passes: `npm run lint:frontend` — Chat.svelte down to 45 errors (was 47; the two removed unused `error` catch params cleared 2 pre-existing lint errors, 0 new). Full-repo lint still crashes pre-existing on FilePreview.svelte (unrelated).
- [x] Existing unit tests pass: `npm run test:frontend` — 131 passed, 10 skipped, 0 failed

#### Manual Verification:
- [ ] Rapidly click between 3+ chats (including an agent web-search chat) faster than they load → the chat that ends up displayed always matches the URL, and no chat is overwritten.
- [ ] On a throttled network (DevTools "Slow 3G"): open chat A, immediately switch to chat C before A finishes loading → C shows C's content; A is not loaded into C.
- [ ] Opening a non-existent / deleted chat id still redirects to `/` (not-found path intact).
- [ ] Normal single navigation and chat creation are unaffected (titles, model selector, tasks, context-usage banner all populate correctly).

**Implementation Note**: Pause for manual confirmation before considering the
work complete.

---

## Testing Strategy

### Unit Tests
None added — no component harness exists and the bug is async reactive timing
inside `Chat.svelte` (per decision). Regression protection comes from the
manual repro plus the existing automated checks (`npm run check`,
`lint:frontend`, `test:frontend`) confirming no breakage.

### Manual Testing Steps (primary verification)
1. **Baseline (pre-fix)**: open an agent chat, run a web search, stop it;
   navigate to another chat; reload it → confirm it now shows the stopped
   web-search chat (reproduces the bug).
2. **Phase 1**: repeat step 1 with the fix → target chat keeps its own content.
3. **Phase 1**: toggle web search within one chat, reload → toggle persists.
4. **Phase 2**: rapid + throttled-network switching across several chats →
   displayed chat always matches URL; no content loss.
5. Confirm not-found redirect and temporary-chat behaviour unchanged.

## Performance Considerations

Phase 1.2 (`lastSavedFeatures` sync) *removes* a redundant full-history
`updateChatById` PUT that currently fires on every chat navigation, slightly
reducing write load. No new overhead is introduced; the added guards are cheap
string/id comparisons.

## Migration Notes

Already-corrupted chats are **not** repaired by this change — their `history`
was overwritten in place and Open WebUI keeps no per-chat version history. If
recovery is required, it must come from a database backup taken before the
corruption; that is an operational task outside this plan. No schema or data
migration is needed for the fix itself.

No new user-facing strings → no i18n (en-US / nl-NL) changes required.

## References

- Corruption vector: `src/lib/components/chat/Chat.svelte:306-318` (commit `c1204faa8c`, 2026-05-17)
- Save sink: `src/lib/components/chat/Chat.svelte:3172-3190` (`saveChatHandler`)
- Load + window opener: `src/lib/components/chat/Chat.svelte:1528-1657`, `:1529` (`loadChat`, `chatId.set`)
- Navigation handler + resets: `src/lib/components/chat/Chat.svelte:217-286`
- Working guard reference: `src/lib/components/chat/Chat.svelte:212-215` (`saveControls` reactive)
