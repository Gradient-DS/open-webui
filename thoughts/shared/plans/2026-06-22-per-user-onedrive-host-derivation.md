# Per-User Multi-Tenant OneDrive: Dynamic SharePoint Host Derivation — Implementation Plan

## Overview

Make the OneDrive/SharePoint file picker derive its host **per-user** from a Microsoft Graph `GET /me/drive` `webUrl` call after MSAL consent, instead of relying solely on the static, per-deployment `ONEDRIVE_SHAREPOINT_URL`. This unlocks **true per-user multi-tenant OneDrive**: a user from *any* Microsoft org can pick from *their own* OneDrive-for-Business files against a single multi-tenant Azure app, with no per-tenant `ONEDRIVE_SHAREPOINT_URL` value required.

The change is **frontend-only**. The backend sync path is already host-independent (it talks to `graph.microsoft.com/v1.0/drives/{drive_id}/...` with `common`-authority delegated tokens), so no backend, config, or migration work is needed.

## Current State Analysis

`ONEDRIVE_SHAREPOINT_URL` is a single per-deployment value that does **double duty**, both consumed only in the frontend:

1. **Picker iframe host** — `OneDriveConfig.getBaseUrl()` builds `https://{host}/_layouts/15/FilePicker.aspx`.
2. **OAuth resource scope** — the MSAL picker token scope is `[`${host}/.default`]`.

Flow today: env → `config.py:3156` (`ONEDRIVE_SHAREPOINT_URL` PersistentConfig) → `/api/config` `onedrive.sharepoint_url` (`main.py:3457`) → `OneDriveConfig.getCredentials()` (`onedrive-file-picker.ts:51`) → `getBaseUrl()`.

### Key Discoveries

- **Single chokepoint for the host:** `src/lib/utils/onedrive-file-picker.ts:109-122` (`getBaseUrl()`). In `organizations` mode it throws `'Sharepoint URL not configured'` when blank (line 112); in `personal` mode it returns the hardcoded `https://onedrive.live.com/picker` (line 120).
- **The 4 picker entry functions** each call `await config.initialize(authorityType)` then synchronously capture `const baseUrl = config.getBaseUrl()` and call `getToken(undefined, authorityType)`:
  - `openOneDrivePicker` (`:452`, `:458`, `:550`)
  - `openOneDriveFilePickerModal` (`:661`, `:665`, `:668`)
  - `openOneDriveFolderPicker` (`:1020`, `:1024`, `:1027`)
  - `openOneDriveItemPicker` (`:1343`, `:1347`, `:1350`) ← the one used by KB cloud sync.
- **A host-independent Graph token path already exists:** `getGraphApiToken()` (`onedrive-file-picker.ts:1658-1701`) acquires `https://graph.microsoft.com/Files.Read.All` using only clientId + authority — **no host required**. This is exactly what the `/me/drive` call needs, and resolves the chicken-and-egg (need a token to learn the host, need the host to scope the picker token).
- **MSAL authority is already tenant-agnostic-capable:** `getMsalInstance()` uses `this.sharepointTenantId || 'common'` (`:67`). AFK's gitops sets `ONEDRIVE_SHAREPOINT_TENANT_ID: "common"` with the multi-tenant app `324f9d97`, so cross-org consent already works at the auth layer.
- **Downloads already derive the per-file host:** `downloadOneDriveFile()` (`:372-440`) extracts the resource from each picked item's `@sharePoint.endpoint` (`:378-389`) via `new URL(endpoint).origin`. This proves the picked-item round-trip is already host-agnostic — the *only* gap is the picker **opening**.
- **`getCredentials()` re-runs on every `initialize()`** and resets `this.sharepointUrl` from `/api/config` (`:51`). A derived host must therefore live in a **separate field** that survives re-init (this is also our per-session memo).
- **Backend is fully host-independent:** `services/onedrive/auth.py` uses `_AUTHORITY_BASE/{tenant_id or 'common'}` + Graph scope `Files.Read.All offline_access`; `services/onedrive/graph_client.py` only ever calls `graph.microsoft.com/v1.0/drives/{drive_id}/...`. No backend change is in scope.
- **Error surfacing:** `cloudSyncHandler` catch (`KnowledgeBase.svelte:856-861`) toasts `$i18n.t('Failed to sync from {{label}}: ', {label}) + error.message`. The appended `error.message` is currently **not** translated; existing thrown strings (`'Sharepoint URL not configured'`, `'Failed to acquire access token'`) are raw English.
- **Test env is `node`** (no `environment` in `vite.config.ts` `test`). `constants.ts` guards every `location` access behind `browser` (`constants.ts:13-14`), so importing browser-adjacent modules at load is safe, but DOM picker functions cannot run under test. Pure helpers in a dependency-free module are trivially testable; existing util tests colocate as `src/lib/utils/*.test.ts` and run via `npm run test:frontend` (`vitest --passWithNoTests`).

### The derivation flow (derive mode)

For `organizations` authority with a **blank** `sharepoint_url`:

1. MSAL login + acquire Graph token via existing `getGraphApiToken('organizations')` (`graph.microsoft.com/Files.Read.All`, host-independent).
2. `GET https://graph.microsoft.com/v1.0/me/drive` with that bearer token.
3. `host = new URL(response.webUrl).origin` → e.g. `https://contoso-my.sharepoint.com`.
4. Memoize on the singleton; use it for both the picker iframe URL and the `{host}/.default` picker token scope.

`/me/drive`'s `webUrl` returns the user's **OneDrive-for-Business `-my` host** (their own org files) — which is the intended "each user's own files, any org" behaviour.

## Desired End State

- With `ONEDRIVE_SHAREPOINT_URL` **blank** + a multi-tenant Azure app (authority `common`/`organizations`) + correct SharePoint delegated permissions, a user from any org opens the OneDrive picker, sees **their own** OneDrive files, picks them, and sync proceeds — with no per-tenant host configured.
- With `ONEDRIVE_SHAREPOINT_URL` **set**, behaviour is byte-for-byte identical to today (no derivation, no extra Graph call).
- The `/me/drive` derivation happens **at most once per page session** (memoized).
- Personal-account flow is unchanged.
- Backend, `/api/config`, DB, and gitops/Helm require no code changes (gitops only needs the documented Azure prereqs + leaving the URL blank for derive-mode tenants).

### Verification of end state

- Automated: new unit tests for host parsing + `/me/drive` fetch/error handling pass; `resolveHost` static/derive/memoize test passes; type-check and lint clean.
- Manual: the AFK-style recipe in "Manual Testing Steps" succeeds (blank host → external-org user → own files → sync started).

## What We're NOT Doing

- **No backend changes.** No `/me/drive` call server-side, no new endpoint, no per-user host storage in `oauth_session`/users, no DB migration. (Considered and rejected: the picker fires before the backend sync-consent flow, so a backend-derived host would arrive too late to set the picker host/scope.)
- **No new config flag.** "Blank `ONEDRIVE_SHAREPOINT_URL` ⇒ derive; set ⇒ static" — the blank value is the opt-in. Existing single-tenant tenants (gradient/kwink/intermax) keep their static value and are untouched.
- **No change to personal accounts** (`onedrive.live.com/picker` path).
- **No change to the picked-item round-trip** (`drive_id`/`item_id`/`@sharePoint.endpoint`) or to `downloadOneDriveFile`.
- **No shared-SharePoint-site discovery.** `/me/drive` yields the user's personal `-my` drive only; selecting arbitrary org SharePoint sites is out of scope.
- **No Azure app reconfiguration in code.** Multi-tenant app + SharePoint delegated perms are documented prerequisites, configured in Azure/gitops, not here.

## Implementation Approach

Extract the two pure/stateless pieces (host parsing + the `/me/drive` fetch) into a new dependency-free module so they can be unit-tested cleanly in the node test env. Keep the stateful orchestration (memoized `derivedHost`, `resolveHost()`, `getBaseUrl()`) in `OneDriveConfig`, composing the helpers. Insert one `await config.resolveHost(authorityType)` call into each picker entry function, right after `initialize()` and before any `getBaseUrl()`/`getToken()` use. Surface derivation failures as actionable, translated toasts. Document the Azure prerequisites.

---

## Phase 1: Dependency-free host helpers + unit tests

### Overview

Create a small module with no `$lib`, `$app`, browser, or MSAL imports, holding the pure host-parsing and the stateless `/me/drive` fetch. This is where the highest-value correctness lives and where testing is cleanest.

### Changes Required

#### 1. New module: `src/lib/utils/onedrive-host.ts`

**Changes:** Add `parseSharepointHostFromWebUrl()` and `fetchOneDriveHost()`.

```ts
// Microsoft Graph endpoint, kept here so the helper has zero app-level deps.
const GRAPH_ME_DRIVE_URL = 'https://graph.microsoft.com/v1.0/me/drive';

/**
 * Derive the SharePoint/OneDrive host origin from a Graph drive `webUrl`.
 * e.g. "https://contoso-my.sharepoint.com/personal/u_contoso/Documents"
 *      -> "https://contoso-my.sharepoint.com"
 * Throws if the value is missing or unparseable.
 */
export function parseSharepointHostFromWebUrl(webUrl: string | undefined | null): string {
	if (!webUrl) {
		throw new Error('OneDrive webUrl missing');
	}
	let parsed: URL;
	try {
		parsed = new URL(webUrl);
	} catch {
		throw new Error('OneDrive webUrl unparseable');
	}
	if (!/^https?:$/.test(parsed.protocol)) {
		throw new Error('OneDrive webUrl unparseable');
	}
	// Always https; origin already drops any trailing path/query.
	return `https://${parsed.host}`;
}

/**
 * Call Graph /me/drive with a Graph-scoped bearer token and return the host origin.
 * Pure of app state; `fetchImpl` is injectable for tests.
 */
export async function fetchOneDriveHost(
	graphToken: string,
	fetchImpl: typeof fetch = fetch
): Promise<string> {
	const res = await fetchImpl(GRAPH_ME_DRIVE_URL, {
		headers: { Authorization: `Bearer ${graphToken}` }
	});
	if (res.status === 404) {
		// No OneDrive provisioned for this account.
		throw new Error('No OneDrive found for your account.');
	}
	if (!res.ok) {
		throw new Error('Could not connect to OneDrive to determine your location.');
	}
	const data = await res.json();
	return parseSharepointHostFromWebUrl(data?.webUrl);
}
```

> Note: the two thrown strings `'No OneDrive found for your account.'` and `'Could not connect to OneDrive to determine your location.'` are the stable, user-facing keys registered for i18n in Phase 3. Keep them verbatim.

#### 2. New test: `src/lib/utils/onedrive-host.test.ts`

**Changes:** Cover parsing and fetch/error handling. No DOM, no MSAL — stub `fetch` per-call.

```ts
import { describe, it, expect, vi } from 'vitest';
import { parseSharepointHostFromWebUrl, fetchOneDriveHost } from './onedrive-host';

describe('parseSharepointHostFromWebUrl', () => {
	it('derives the -my host from a personal OneDrive webUrl', () => {
		expect(
			parseSharepointHostFromWebUrl(
				'https://contoso-my.sharepoint.com/personal/u_contoso_com/Documents'
			)
		).toBe('https://contoso-my.sharepoint.com');
	});
	it('strips query strings', () => {
		expect(parseSharepointHostFromWebUrl('https://x-my.sharepoint.com/a?b=c')).toBe(
			'https://x-my.sharepoint.com'
		);
	});
	it('upgrades http to https', () => {
		expect(parseSharepointHostFromWebUrl('http://x-my.sharepoint.com/a')).toBe(
			'https://x-my.sharepoint.com'
		);
	});
	it('throws on missing or unparseable webUrl', () => {
		expect(() => parseSharepointHostFromWebUrl(undefined)).toThrow();
		expect(() => parseSharepointHostFromWebUrl('')).toThrow();
		expect(() => parseSharepointHostFromWebUrl('not a url')).toThrow();
	});
});

describe('fetchOneDriveHost', () => {
	const ok = (webUrl: unknown) =>
		({ ok: true, status: 200, json: async () => ({ webUrl }) }) as unknown as Response;

	it('returns the host origin on 200', async () => {
		const f = vi.fn().mockResolvedValue(ok('https://contoso-my.sharepoint.com/personal/u/Documents'));
		await expect(fetchOneDriveHost('tok', f)).resolves.toBe('https://contoso-my.sharepoint.com');
		expect(f).toHaveBeenCalledWith(
			'https://graph.microsoft.com/v1.0/me/drive',
			expect.objectContaining({ headers: { Authorization: 'Bearer tok' } })
		);
	});
	it('throws a clear error on 404 (no OneDrive provisioned)', async () => {
		const f = vi.fn().mockResolvedValue({ ok: false, status: 404 } as Response);
		await expect(fetchOneDriveHost('tok', f)).rejects.toThrow('No OneDrive found for your account.');
	});
	it('throws on other non-ok responses', async () => {
		const f = vi.fn().mockResolvedValue({ ok: false, status: 500 } as Response);
		await expect(fetchOneDriveHost('tok', f)).rejects.toThrow(
			'Could not connect to OneDrive to determine your location.'
		);
	});
	it('throws when webUrl is missing in the payload', async () => {
		const f = vi.fn().mockResolvedValue(ok(undefined));
		await expect(fetchOneDriveHost('tok', f)).rejects.toThrow();
	});
});
```

### Success Criteria

#### Automated Verification

- [ ] New tests pass: `npm run test:frontend -- src/lib/utils/onedrive-host.test.ts`
- [ ] Full unit suite still green: `npm run test:frontend`
- [ ] Type-check passes for the new module: `npm run check`
- [ ] Lint clean: `npm run lint:frontend`

#### Manual Verification

- [ ] N/A for this phase (pure helpers; covered by automated tests).

**Implementation Note**: After automated verification passes, proceed to Phase 2 (no manual gate needed for pure helpers).

---

## Phase 2: Wire derivation into the picker (`onedrive-file-picker.ts`)

### Overview

Add a memoized `derivedHost`, an async `resolveHost()` that composes `getGraphApiToken()` + `fetchOneDriveHost()`, make `getBaseUrl()` prefer the static URL then the derived host, and call `resolveHost()` in each of the 4 picker entry functions before the host/scope are used.

### Changes Required

#### 1. `OneDriveConfig` class

**File**: `src/lib/utils/onedrive-file-picker.ts`

**Changes:**

- Import the helper at the top:

```ts
import { fetchOneDriveHost } from './onedrive-host';
```

- Add the memo field (near `:9`):

```ts
private derivedHost: string | null = null;
```

- Add `resolveHost()` (new public method on the class). Must run **after** `initialize()` and **before** any `getBaseUrl()`/`getToken()` call:

```ts
/**
 * Ensure an effective SharePoint host is available for `organizations` mode.
 * - Static mode (sharepoint_url set): no-op, getBaseUrl() uses it.
 * - Derive mode (sharepoint_url blank): derive once per session from Graph /me/drive.
 * - Personal mode: no-op (getBaseUrl returns the fixed consumer picker host).
 */
public async resolveHost(authorityType?: 'personal' | 'organizations'): Promise<void> {
	await this.ensureInitialized(authorityType);
	if (this.currentAuthorityType !== 'organizations') return; // personal unaffected
	if (this.sharepointUrl && this.sharepointUrl !== '') return; // static mode
	if (this.derivedHost) return; // memoized for the session

	const graphToken = await getGraphApiToken(authorityType); // host-independent
	this.derivedHost = await fetchOneDriveHost(graphToken);
}
```

- Change `getBaseUrl()` (`:109-122`) to prefer static, then derived:

```ts
public getBaseUrl(): string {
	if (this.currentAuthorityType === 'organizations') {
		const host = this.sharepointUrl || this.derivedHost;
		if (!host || host === '') {
			throw new Error('Sharepoint URL not configured');
		}
		const sharePointBaseUrl = host.replace(/^https?:\/\//, '').replace(/\/$/, '');
		return `https://${sharePointBaseUrl}`;
	} else {
		return 'https://onedrive.live.com/picker';
	}
}
```

> `getGraphApiToken` is defined later in the module (function hoisting makes it callable from the class method at runtime). `resolveHost` deliberately does **not** clear `derivedHost` on `getCredentials()` re-runs, which is what makes it survive the per-call `initialize()` and act as the session memo.

#### 2. Call `resolveHost()` in each picker entry function

**File**: `src/lib/utils/onedrive-file-picker.ts`

**Changes:** In each of the 4 functions, insert the resolve call immediately after `await config.initialize(authorityType)` and before the existing `const baseUrl = config.getBaseUrl()` / first `getToken(...)`:

- `openOneDrivePicker` — after `:452`
- `openOneDriveFilePickerModal` — after `:661`
- `openOneDriveFolderPicker` — after `:1020`
- `openOneDriveItemPicker` — after `:1343`

```ts
await config.initialize(authorityType);
await config.resolveHost(authorityType); // ← new: derive host per-user if not statically configured
```

Downstream code is unchanged: `getBaseUrl()` now returns the resolved host, and `getToken(undefined, authorityType)`'s `[`${config.getBaseUrl()}/.default`]` scope (`:137`, `:183`) transparently uses it.

#### 3. `resolveHost` orchestration test

**File**: `src/lib/utils/onedrive-file-picker.test.ts` (new)

**Changes:** Verify static vs derive vs memoize without driving the DOM picker. Mock `@azure/msal-browser` (so `getMsalInstance`/`getGraphApiToken` resolve a fake token) and stub global `fetch` for both `/api/config` and `/me/drive`.

```ts
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('@azure/msal-browser', () => {
	class PublicClientApplication {
		async initialize() {}
		async acquireTokenSilent() {
			return { accessToken: 'graph-token', account: {} };
		}
		setActiveAccount() {}
	}
	return { PublicClientApplication };
});

// Config payload helper; sharepoint_url toggles static vs derive.
function stubConfig(sharepointUrl: string) {
	return async (url: string) => {
		if (typeof url === 'string' && url.endsWith('/api/config')) {
			return {
				ok: true,
				json: async () => ({
					onedrive: {
						client_id_business: 'biz-client',
						client_id_personal: 'personal-client',
						sharepoint_url: sharepointUrl,
						sharepoint_tenant_id: 'common'
					}
				})
			} as unknown as Response;
		}
		if (typeof url === 'string' && url.endsWith('/me/drive')) {
			return {
				ok: true,
				status: 200,
				json: async () => ({ webUrl: 'https://derived-my.sharepoint.com/personal/u/Documents' })
			} as unknown as Response;
		}
		throw new Error(`unexpected fetch: ${url}`);
	};
}

describe('OneDriveConfig.resolveHost', () => {
	beforeEach(() => {
		vi.resetModules(); // fresh singleton per test
	});

	it('uses the static host when sharepoint_url is set (no /me/drive call)', async () => {
		const fetchSpy = vi.fn(stubConfig('https://static.sharepoint.com'));
		vi.stubGlobal('fetch', fetchSpy);
		const mod = await import('./onedrive-file-picker');
		// Access the singleton via a small exported test hook or getInstance equivalent.
		// (Implementation: expose getBaseUrl()/resolveHost() through the singleton.)
		// ...assert getBaseUrl() === 'https://static.sharepoint.com'
		// ...assert no fetch call ended with '/me/drive'
	});

	it('derives the host once when sharepoint_url is blank (memoized)', async () => {
		const fetchSpy = vi.fn(stubConfig(''));
		vi.stubGlobal('fetch', fetchSpy);
		const mod = await import('./onedrive-file-picker');
		// ...call resolveHost('organizations') twice
		// ...assert getBaseUrl() === 'https://derived-my.sharepoint.com'
		// ...assert exactly one '/me/drive' fetch across both calls (memoization)
	});
});
```

> The singleton is currently created via the private-constructor `getInstance()`. To make `resolveHost`/`getBaseUrl` assertable, either (a) the methods are already public (they are) and we exercise them through `OneDriveConfig.getInstance()` if it's exported, or (b) add a minimal test-only export. Choose (a) if `getInstance` is reachable; otherwise add `export { OneDriveConfig }` (it is not currently exported). Prefer the smallest surface that lets the test call `resolveHost()` then `getBaseUrl()`. This is an implementation detail to settle during coding; the assertions above are the contract.

### Success Criteria

#### Automated Verification

- [ ] Picker tests pass: `npm run test:frontend -- src/lib/utils/onedrive-file-picker.test.ts`
- [ ] Full unit suite green: `npm run test:frontend`
- [ ] Type-check passes: `npm run check`
- [ ] Lint clean: `npm run lint:frontend`
- [ ] Production build compiles: `npm run build`

#### Manual Verification

- [ ] **Static mode unchanged:** on a tenant with `ONEDRIVE_SHAREPOINT_URL` set (e.g. local config mirroring gradient), the picker opens exactly as before; no `/me/drive` request appears in the network tab.
- [ ] **Derive mode (primary):** with `ONEDRIVE_SHAREPOINT_URL` blank + multi-tenant app, opening the OneDrive picker triggers a Graph login, a single `GET /me/drive`, and the picker opens against the user's `-my` host showing *their own* files.

**Implementation Note**: After automated verification passes, pause for the human to run the manual checks (especially derive mode, which requires a real multi-tenant Azure app) before proceeding to Phase 3.

---

## Phase 3: Error surfacing + i18n

### Overview

Register the two new user-facing strings in both locales and make the sync-error toast translate the appended picker message, so derivation failures show actionable, Dutch-translated text instead of raw English.

### Changes Required

#### 1. i18n keys

**Files**: `src/lib/i18n/locales/en-US/translation.json`, `src/lib/i18n/locales/nl-NL/translation.json`

**Changes:** Add the two keys (alphabetically sorted; en-US value empty = use key itself):

en-US:
```json
"Could not connect to OneDrive to determine your location.": "",
"No OneDrive found for your account.": "",
```

nl-NL:
```json
"Could not connect to OneDrive to determine your location.": "Kon geen verbinding maken met OneDrive om je locatie te bepalen.",
"No OneDrive found for your account.": "Geen OneDrive gevonden voor je account.",
```

> Place each key in correct alphabetical position. Run `npm run i18n:parse` afterward to confirm no key churn.

#### 2. Translate the appended error message in the sync toast

**File**: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte`

**Changes:** In `cloudSyncHandler`'s catch (`:858-861`), wrap the appended message through `$i18n.t()`. Because the project's i18next returns the input string unchanged when no key matches, this is a safe passthrough for all providers and makes thrown picker errors translatable:

```svelte
toast.error(
	$i18n.t('Failed to sync from {{label}}: ', { label: provider.label }) +
		$i18n.t(error instanceof Error ? error.message : String(error))
);
```

> Optional, recommended for consistency: apply the same `$i18n.t(...)` wrap to the analogous catches in `cloudResyncHandler` and the other provider catch blocks (`:934-936`, `:1018-1020`, `:1378-1380`). Keep within OneDrive scope if you prefer minimal touch; the derive errors flow through `cloudSyncHandler`.

### Success Criteria

#### Automated Verification

- [ ] i18n parse is stable (no unexpected additions/removals): `npm run i18n:parse` then `git diff --stat` shows only the intended keys
- [ ] Type-check passes: `npm run check`
- [ ] Lint clean: `npm run lint:frontend`
- [ ] Build compiles: `npm run build`

#### Manual Verification

- [ ] In derive mode, forcing a `/me/drive` 404 (e.g. an account with no OneDrive) shows the toast "Failed to sync from OneDrive: No OneDrive found for your account." (and the Dutch equivalent with the nl-NL locale active).
- [ ] A blank-host org with derivation disabled at the Azure level (missing SharePoint delegated perms) shows a clear failure, not a silent hang.

**Implementation Note**: After automated verification, pause for the human to confirm the toast wording/translation in the UI before Phase 4.

---

## Phase 4: Documentation — Azure prerequisites + manual verification

### Overview

Record the Azure/gitops prerequisites that make derive mode work, so whoever configures a tenant (AFK first) knows what's required, and capture the manual verification recipe.

### Changes Required

#### 1. Integration cookbook OneDrive note

**File**: `collab/docs/external-integration-cookbook.md`

**Changes:** Add a short "Per-user multi-tenant OneDrive (host derivation)" subsection to the OneDrive material covering:

- **Trigger:** leave `ONEDRIVE_SHAREPOINT_URL` blank ⇒ host derived per-user from Graph `/me/drive`; set it ⇒ static host as before.
- **Azure app prerequisites:**
  - App registration must be **multi-tenant** (and `ONEDRIVE_SHAREPOINT_TENANT_ID` set to `common`/`organizations`, or blank → `common`) so users from any org can consent.
  - App must hold **SharePoint/Office365 delegated** permissions (e.g. `MyFiles.Read` / `AllSites.Read`) in addition to Graph `Files.Read.All`, so the `{host}/.default` picker-token scope is grantable. Without these, the picker token acquisition fails after the host is derived.
- **Reference:** the existing rationale in `soev-gitops/tenants/previder-prod/afk/helmrelease.yaml` (the multi-tenant `common` + blank-URL block).
- **Scope note:** derivation yields the user's own `-my` OneDrive; shared SharePoint sites are not discovered.

#### 2. (If applicable) gitops AFK comment touch-up

**File**: `soev-gitops/tenants/previder-prod/afk/helmrelease.yaml` (separate repo — out of this repo's PR; note for follow-up)

**Changes:** Once shipped, update the inline comment to state that blank `onedriveSharepointUrl` now triggers per-user derivation (no longer a hard-fail), pending the SharePoint delegated-perms prerequisite. Track as a gitops follow-up, not part of this open-webui PR.

### Success Criteria

#### Automated Verification

- [ ] N/A (documentation only).

#### Manual Verification

- [ ] Cookbook subsection is accurate and references the AFK gitops block.
- [ ] Prerequisites are specific enough that a fresh tenant can be configured from the doc alone.

---

## Testing Strategy

### Unit Tests

- `parseSharepointHostFromWebUrl`: `-my` host, trailing path, query string, `http`→`https`, missing/unparseable input (throws).
- `fetchOneDriveHost`: 200 → host origin + correct Graph URL/Authorization header; 404 → "No OneDrive found for your account."; other non-ok → "Could not connect…"; missing `webUrl` → throws.
- `OneDriveConfig.resolveHost`: static mode (no `/me/drive` call), derive mode (host set from `webUrl`), memoization (single `/me/drive` across repeated calls).

### Integration / Manual Tests

MSAL + the FilePicker iframe cannot be meaningfully automated here, so these are manual:

### Manual Testing Steps

1. **Static regression:** Configure `ONEDRIVE_SHAREPOINT_URL=https://<tenant>.sharepoint.com` (single-tenant app). Open a KB → OneDrive sync → confirm the picker opens as today, files list, sync starts, and **no** `/me/drive` request appears.
2. **Derive happy path (AFK-style):** Set `ONEDRIVE_SHAREPOINT_URL=""`, point at the multi-tenant app (`ONEDRIVE_SHAREPOINT_TENANT_ID=common`) with SharePoint delegated perms consented. Log in as a user from an external org. Open OneDrive sync → confirm: one Graph login, exactly one `GET /me/drive`, picker opens against the user's `…-my.sharepoint.com` host, the user's **own** files are listed, selection + sync start succeed.
3. **Memoization:** In the same session, open the picker again → confirm no second `/me/drive` request.
4. **No-OneDrive account:** Use an account without a provisioned OneDrive → confirm the toast reads "Failed to sync from OneDrive: No OneDrive found for your account." (and Dutch under nl-NL).
5. **Missing SharePoint perms:** With the app lacking SharePoint delegated perms, confirm derivation succeeds but the subsequent picker-token step fails with a visible error (not a hang).

## Performance Considerations

- One extra Graph round-trip (`/me/drive`) per session **only** in derive mode, gated by memoization. Static mode adds nothing. The Graph token is acquired through the existing `getGraphApiToken` path (MSAL-cached), so no extra interactive prompt beyond the one already needed for the picker — except a possible first-time consent for the Graph scope.

## Migration Notes

- No data migration. No backend or config schema change. Existing tenants with a set `ONEDRIVE_SHAREPOINT_URL` are unaffected. Enabling derive mode for a tenant is purely: (1) ensure the Azure app is multi-tenant with SharePoint delegated perms, (2) leave `ONEDRIVE_SHAREPOINT_URL` blank in gitops.

## References

- Picker host chokepoint: `src/lib/utils/onedrive-file-picker.ts:109-122` (`getBaseUrl`), scope use `:137`,`:183`, entry functions `:452`,`:661`,`:1020`,`:1343`, existing Graph token `:1658-1701`, per-file host derivation `:372-440`.
- Config flow: `backend/open_webui/config.py:3156-3166`, `backend/open_webui/main.py:3454-3458`, admin `backend/open_webui/routers/configs.py:1175-1234`.
- Host-independent backend: `backend/open_webui/services/onedrive/auth.py:31-33,66,96`, `services/onedrive/graph_client.py`.
- Error surface: `src/lib/components/workspace/Knowledge/KnowledgeBase.svelte:744-865` (`cloudSyncHandler`).
- Real per-tenant host/GUID values + the AFK multi-tenant case: `soev-gitops/tenants/previder-prod/{gradient,kwink,afk}/helmrelease.yaml`, `intermax-prod/soev-test/helmrelease.yaml`.
- Prior multi-tenant open question: `thoughts/shared/research/2026-01-15-onedrive-openwebui-integration.md:808`; existing per-item `webUrl` capture: `thoughts/shared/plans/2026-01-15-onedrive-openwebui-collection-sync.md:104,208`.
