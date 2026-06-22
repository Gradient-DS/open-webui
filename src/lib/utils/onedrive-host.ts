// Pure, dependency-free helpers for deriving a user's OneDrive/SharePoint host.
//
// Kept free of any $lib / $app / browser / MSAL imports so they can be unit
// tested directly in the node test environment. The stateful orchestration
// (memoization, MSAL token acquisition) lives in onedrive-file-picker.ts and
// composes these.

// Microsoft Graph endpoint for the signed-in user's default drive.
const GRAPH_ME_DRIVE_URL = 'https://graph.microsoft.com/v1.0/me/drive';

/**
 * Derive the SharePoint/OneDrive host origin from a Graph drive `webUrl`.
 *
 * e.g. "https://contoso-my.sharepoint.com/personal/u_contoso/Documents"
 *      -> "https://contoso-my.sharepoint.com"
 *
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

	// Always https; `.host` already excludes any path/query.
	return `https://${parsed.host}`;
}

/**
 * Call Graph /me/drive with a Graph-scoped bearer token and return the host
 * origin. Stateless and pure of app config; `fetchImpl` is injectable for tests.
 *
 * The thrown messages are stable, user-facing strings registered for i18n.
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
