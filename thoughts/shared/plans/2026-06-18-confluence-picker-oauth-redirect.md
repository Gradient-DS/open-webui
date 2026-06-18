# Confluence Chat Picker — OAuth Consent Redirect Fix

## Overview

When Confluence Cloud Sync OAuth is configured "on request" (admin sets client id/secret; each user authorizes individually), a user who opens the Confluence picker from the chat input **"+"** menu without an existing token never reaches the OAuth consent screen. The picker calls `listSites()`, gets a 401, and shows a dead-end error message instead of starting authorization.

This plan makes the Confluence picker **self-authorizing**: on a 401, it auto-opens the existing OAuth consent popup, then retries once. The fix is **frontend-only** — the backend already stores Confluence tokens per-user, so any successful `/auth/initiate` produces a token the picker can immediately read.

## Current State Analysis

**The broken path (chat "+" menu):**

- `src/lib/components/chat/MessageInput.svelte:2076` — the Confluence "+" menu button calls `confluenceHandler`.
- `src/lib/components/chat/MessageInput.svelte:702-704` — `confluenceHandler` just sets `showConfluencePicker = true`. No token check, no auth trigger.
- `src/lib/components/workspace/Knowledge/ConfluencePickerModal.svelte:78-101` — `bootstrap()` immediately calls `listSites(localStorage.token)`. On failure it sets `loadError` (lines 91-97) and renders a static *"Connect your Confluence account in Settings, then reopen this picker."* message (lines 420-428). **No OAuth flow is ever offered.**

**Why OneDrive / Google Drive don't have this problem:** their picker utilities acquire a token *before* loading content and pop consent on failure — `google-drive-picker.ts` (`getAuthToken → triggerAuthPopup`) and `onedrive-file-picker.ts` (`getToken → loginPopup`). The auth logic lives *inside the picker*, so every caller gets it. Confluence put that logic only in the *callers* (KnowledgeBase, admin), not the modal.

**Why the KB and admin callers work** (they share the same modal):

- `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte:801-829` — the `confluence` branch checks `getTokenStatus(localStorage.token, knowledge.id)` and calls `authorizeBackgroundSync(provider)` (which opens `/auth/initiate?knowledge_id=${knowledge.id}` — `KnowledgeBase.svelte:1303-1357`) **before** opening the picker.
- `src/lib/components/admin/Settings/CloudSync/ConfluenceSection.svelte:290-324` — `connectConfluenceAccount()` opens `/confluence/auth/initiate` (no knowledge_id) and listens for the `confluence_auth_callback` postMessage.

The chat caller simply omits this pre-flight step.

### Key Discoveries

- **Tokens are stored per-user, not per-KB.** `OAuthSession` is keyed by `(user_id, provider)` only — `backend/open_webui/models/oauth_sessions.py:26-42`. `get_valid_access_token(user_id, knowledge_id)` **ignores `knowledge_id`** for lookup; it fetches `get_session_by_provider_and_user_id('confluence', user_id)` — `backend/open_webui/services/sync/token_refresh.py:29-74`. So *any* successful authorization yields a token the picker can read.
- **The picker reads via `__picker__`** — `/browse/sites` → `_picker_client(user)` → `get_valid_access_token(user.id, knowledge_id='__picker__')` raises `HTTPException(401, 'No valid Confluence token. Please re-authorize.')` when there's no token — `backend/open_webui/routers/confluence_sync.py:510-522, 560-581`.
- **`/auth/initiate` with no `knowledge_id` works and defaults to `__general__`** — `backend/open_webui/routers/confluence_sync.py:334-360`. Because storage ignores `knowledge_id`, the `__general__` token is readable by the `__picker__` lookup. **Do not pass `knowledge_id=__picker__`**: line 355 runs `if knowledge_id: await get_knowledge_or_raise(...)`, and `__picker__` is not a real KB → it would 404. Omit the param entirely.
- **`/auth/token-status/{knowledge_id}` is unusable for the chat picker** — `handle_get_token_status` does `Knowledges.get_knowledge_by_id` and 404s if the id isn't a real KB owned by the user (`backend/open_webui/services/sync/router.py:267-293`). The chat picker has no KB id, so a *proactive* token-status check isn't viable. The fix must be **reactive** (catch the 401), which also mirrors Google Drive's pattern.
- **The callback already postMessages the right shape** — `{ type: 'confluence_auth_callback', success: bool, ... }` then `window.close()` — `backend/open_webui/services/sync/router.py:365-391`. No backend work needed.
- **Picker proxy errors lose the status code.** The local `apiFetch` in `src/lib/apis/confluence/index.ts:71-78` throws a bare `Error(detail)`, so the modal can't distinguish a 401 from any other failure without string-matching. We'll attach the HTTP status to the thrown error.

## Desired End State

A user with OAuth configured but no personal token opens the Confluence picker from the chat "+" menu → the Atlassian consent popup opens automatically → after they consent, the picker loads their sites/spaces/pages and works normally. If the browser blocks the auto-popup, the modal shows a **"Connect Confluence"** button that opens consent on a direct click. Already-authorized users see pages immediately (no regression). The KnowledgeBase and admin pickers are unaffected (their pre-flight auth means the new 401 path never fires for them).

**Verification:** see Success Criteria per phase and the Testing Strategy.

## What We're NOT Doing

- **No backend changes.** The token model and endpoints already support this.
- **Not refactoring `ConfluenceSection.connectConfluenceAccount` or `KnowledgeBase.authorizeBackgroundSync`** to use the new shared helper (decision: "Modal only + shared helper"). Their duplicated popup code stays as-is for now; deduping is a possible later cleanup.
- **Not adding a proactive token-status pre-check** to the chat handler — unusable without a real KB id; the reactive approach is correct.
- **Not touching OneDrive / Google Drive** pickers.
- **Not changing the "+" menu visibility gating** (`confluence_oauth_configured` etc. in `MessageInput.svelte:2071`).

## Implementation Approach

Move the auth handling *into the picker modal* (matching the OneDrive/GDrive architecture) but in the minimal form: a reactive 401 → auto-open consent → retry. Add one reusable popup helper plus error-status plumbing in the Confluence API client so the modal can detect the 401 cleanly. The modal change is universally safe because the new branch only activates on a 401, which the KB/admin callers never produce (they pre-authorize).

---

## Phase 1: API client — auth-error detection + shared popup helper

### Overview

Make picker-proxy failures carry their HTTP status, and add a single `authorizeConfluencePopup()` helper that opens the consent popup and resolves when it closes.

### Changes Required

#### 1. Confluence API client

**File**: `src/lib/apis/confluence/index.ts`
**Changes**: Attach HTTP status to thrown errors; export an auth-error predicate and the popup helper.

Replace the local `apiFetch` (lines 71-78) and add exports:

```ts
export class ConfluenceApiError extends Error {
	status: number;
	constructor(message: string, status: number) {
		super(message);
		this.name = 'ConfluenceApiError';
		this.status = status;
	}
}

async function apiFetch<T>(url: string, init?: RequestInit): Promise<T> {
	const res = await fetch(url, init);
	if (!res.ok) {
		const error = await res.json().catch(() => ({ detail: res.statusText }));
		throw new ConfluenceApiError(error.detail || `Request failed: ${res.status}`, res.status);
	}
	return res.json();
}

// 401 from the picker proxy = the user has no valid Confluence token yet.
export function isConfluenceAuthError(e: unknown): boolean {
	return e instanceof ConfluenceApiError && e.status === 401;
}

export type ConfluenceAuthResult = 'authorized' | 'cancelled' | 'blocked';

// Open the Atlassian OAuth consent popup for per-user Confluence access and
// resolve once it closes. `knowledge_id` is deliberately omitted: the backend
// defaults it to '__general__', and the token is stored per-user
// (oauth_session keyed by user_id+provider), so it is immediately usable by the
// picker's /browse/sites lookup. Passing knowledge_id=__picker__ would 404 —
// /auth/initiate validates real KB ids and __picker__ is a pseudo-id.
export function authorizeConfluencePopup(): Promise<ConfluenceAuthResult> {
	return new Promise((resolve) => {
		const popup = window.open(
			`${base}/auth/initiate`,
			'confluence_auth',
			'width=600,height=700,scrollbars=yes'
		);
		if (!popup) {
			resolve('blocked');
			return;
		}
		let result: ConfluenceAuthResult = 'cancelled';
		const onMessage = (event: MessageEvent) => {
			if (event.data?.type !== 'confluence_auth_callback') return;
			result = event.data.success ? 'authorized' : 'cancelled';
		};
		window.addEventListener('message', onMessage);
		// Source of truth is whether the popup closed; postMessage can be missed
		// on origin mismatch, so the caller re-checks by retrying listSites().
		const timer = setInterval(() => {
			if (!popup.closed) return;
			clearInterval(timer);
			window.removeEventListener('message', onMessage);
			resolve(result);
		}, 500);
	});
}
```

(`base` is already defined at `src/lib/apis/confluence/index.ts:69` as `${WEBUI_API_BASE_URL}/confluence`.)

### Success Criteria

#### Automated Verification:
- [ ] Type check passes for the touched file: `npm run check` introduces no new errors in `src/lib/apis/confluence/index.ts`
- [ ] Lint passes: `npm run lint:frontend`
- [ ] Build succeeds: `npm run build`

#### Manual Verification:
- [ ] None for this phase (pure API-layer addition; exercised in Phase 2)

---

## Phase 2: Picker modal — auto-open consent + click-to-connect fallback

### Overview

In `ConfluencePickerModal.svelte`, on a 401 from `listSites()`, auto-open the consent popup and retry once. If the popup is blocked or the user cancels, show a **"Connect Confluence"** button (a direct click reliably bypasses popup blockers).

### Changes Required

#### 1. Picker modal — imports & state

**File**: `src/lib/components/workspace/Knowledge/ConfluencePickerModal.svelte`
**Changes**: Import the new helpers; add a `needsAuth` state flag.

Extend the existing import from `$lib/apis/confluence` (lines 12-20) with `authorizeConfluencePopup` and `isConfluenceAuthError`. Add near `loadError` (line 42):

```ts
// Set when the user has no Confluence token and the auto-popup couldn't
// complete it (blocked or cancelled) — render a click-to-connect affordance.
let needsAuth = false;
```

#### 2. Picker modal — `bootstrap()` becomes auth-aware

**File**: `src/lib/components/workspace/Knowledge/ConfluencePickerModal.svelte`
**Changes**: Split the site-load into `loadSites()`; on 401 auto-open consent then retry; add a `connectConfluence()` click handler.

Replace `bootstrap()` (lines 78-101):

```ts
// Loads accessible sites; throws on failure (incl. 401 when the user has no
// Confluence token). Split out so the auth-retry path can reuse it.
async function loadSites() {
	const res = await listSites(localStorage.token);
	sites = res.sites ?? [];
	if (sites.length === 0) {
		loadError = $i18n.t('No Confluence sites are accessible for this account.');
		return;
	}
	if (sites.length === 1) {
		await selectSite(sites[0]);
	}
}

async function bootstrap() {
	loading = true;
	loadError = '';
	needsAuth = false;
	try {
		await loadSites();
	} catch (e) {
		// 401 → user hasn't connected Confluence. Auto-open the consent popup
		// (mirrors the Google Drive picker), then retry once. The per-user token
		// the popup stores is immediately readable by /browse/sites.
		if (isConfluenceAuthError(e)) {
			const result = await authorizeConfluencePopup();
			if (result === 'blocked') {
				// window.open after the await loses the user gesture and some
				// browsers block it — fall back to a click-to-connect button.
				needsAuth = true;
				return;
			}
			try {
				await loadSites();
			} catch (e2) {
				if (isConfluenceAuthError(e2)) {
					needsAuth = true; // cancelled or still no token
				} else {
					loadError =
						$i18n.t('Failed to load Confluence sites: ') +
						(e2 instanceof Error ? e2.message : String(e2));
				}
			}
		} else {
			loadError =
				$i18n.t('Failed to load Confluence sites: ') +
				(e instanceof Error ? e.message : String(e));
		}
	} finally {
		loading = false;
	}
}

// Click-to-connect fallback. Opening the popup directly from the click keeps
// the user gesture, so it isn't blocked.
async function connectConfluence() {
	const result = await authorizeConfluencePopup();
	if (result === 'blocked') {
		toast.error($i18n.t('Please allow popups to connect Confluence.'));
		return;
	}
	booted = true; // keep the open-guard latched while we retry
	await bootstrap();
}
```

#### 3. Picker modal — reset `needsAuth` on close

**File**: `src/lib/components/workspace/Knowledge/ConfluencePickerModal.svelte`
**Changes**: Extend the close-reset reactive block (lines 375-378):

```ts
$: if (!show) {
	booted = false;
	loadError = '';
	needsAuth = false;
}
```

#### 4. Picker modal — not-connected UI

**File**: `src/lib/components/workspace/Knowledge/ConfluencePickerModal.svelte`
**Changes**: Add a `needsAuth` branch ahead of the `loadError` branch in the content area (before line 420 `{:else if loadError}`):

```svelte
{:else if needsAuth}
	<div
		class="flex flex-col items-center justify-center h-full gap-3 text-sm text-gray-500 text-center px-6"
	>
		<Confluence className="size-8 opacity-60" />
		<div class="text-gray-700 dark:text-gray-300 font-medium">
			{$i18n.t('Connect your Confluence account to browse spaces and pages.')}
		</div>
		<button
			class="px-3 py-1.5 rounded-lg bg-gray-800 text-white hover:bg-gray-700 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white"
			on:click={connectConfluence}
		>
			{$i18n.t('Connect Confluence')}
		</button>
	</div>
{:else if loadError}
```

(`Confluence` icon and `toast` are already imported — `ConfluencePickerModal.svelte:3,10`.)

#### 5. i18n strings

**Files**: `src/lib/i18n/locales/en-US/translation.json`, `src/lib/i18n/locales/nl-NL/translation.json`
**Changes**: Add (alphabetically sorted) keys in both locales:

| Key (en-US) | nl-NL |
|---|---|
| `Connect Confluence` | `Confluence verbinden` |
| `Connect your Confluence account to browse spaces and pages.` | `Verbind je Confluence-account om ruimtes en pagina's te bekijken.` |
| `Please allow popups to connect Confluence.` | `Sta pop-ups toe om Confluence te verbinden.` |

(en-US convention allows empty string = "use the key itself"; provide the English text for clarity and the Dutch translations per project i18n policy.)

### Success Criteria

#### Automated Verification:
- [ ] Type check introduces no new errors in `ConfluencePickerModal.svelte`: `npm run check`
- [ ] Lint passes: `npm run lint:frontend`
- [ ] Build succeeds: `npm run build`
- [ ] New i18n keys present in both locales: `grep -c "Connect Confluence" src/lib/i18n/locales/en-US/translation.json src/lib/i18n/locales/nl-NL/translation.json`

#### Manual Verification:
- [ ] **Fresh user, no token:** open chat "+" menu → Confluence → consent popup opens automatically; after consenting, the picker loads sites/spaces/pages and selecting a page attaches it to chat.
- [ ] **Popup blocked:** with popups blocked, the same flow shows the "Connect Confluence" button; clicking it opens consent (not blocked), and after consent the picker loads.
- [ ] **User cancels consent** (closes popup without authorizing): the "Connect Confluence" button is shown; a second attempt works.
- [ ] **Already-authorized user:** picker loads pages immediately — no popup, no regression.
- [ ] **No regression in KnowledgeBase Confluence picker** (it pre-authorizes; the new 401 branch never fires).
- [ ] **No regression in admin Cloud Sync → Confluence shared-KB picker.**
- [ ] **Genuine non-auth error** (e.g. backend down) still shows the `loadError` message, not the connect button.

**Implementation Note**: After Phase 2 automated verification passes, pause for manual confirmation of the consent flow (it requires a live Atlassian OAuth app and a running stack) before considering the work done.

---

## Testing Strategy

### Manual Testing Steps
1. Configure Confluence OAuth (client id/secret) in the Cloud Sync admin tab without authorizing a personal token for the test user.
2. As that user, open the chat "+" menu and click Confluence → verify the consent popup opens automatically.
3. Complete consent → verify sites/spaces/pages load and a selected page attaches to the chat.
4. Repeat with the browser's popup blocker enabled → verify the "Connect Confluence" button path.
5. Cancel the consent popup once → verify the button fallback, then succeed on retry.
6. Re-open the picker as the now-authorized user → verify it loads pages directly.
7. Open the KnowledgeBase Confluence picker and the admin shared-KB picker → verify unchanged behavior.

### Notes
- No new unit tests are added; the change is UI-flow glue over existing endpoints. If a Vitest harness for `$lib/apis/confluence` is desired, `isConfluenceAuthError` and `ConfluenceApiError` are pure and trivially testable.

## Performance Considerations

None. The auto-popup adds a 500ms `setInterval` poll that lives only while the consent popup is open and is cleared on close.

## Migration Notes

None — no schema or config changes. Behavior is additive and gated on a 401 that authorized users never hit.

## References

- Bug pinpoint (this investigation): chat handler `MessageInput.svelte:702-704`; modal `ConfluencePickerModal.svelte:78-101`.
- Working reference patterns: `KnowledgeBase.svelte:801-829, 1303-1357`; `ConfluenceSection.svelte:290-324`; `google-drive-picker.ts:82-133`; `onedrive-file-picker.ts:126-169`.
- Backend token model: `models/oauth_sessions.py:26-42`; `services/sync/token_refresh.py:29-74`; `routers/confluence_sync.py:334-360, 510-522, 560-581`; callback HTML `services/sync/router.py:365-391`.
- Related plan: `thoughts/shared/plans/2026-05-21-confluence-cloud-sync-admin.md`.
