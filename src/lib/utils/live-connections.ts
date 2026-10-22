import type { DocumentReference } from './onedrive-file-picker';
import type { ChatAttachment } from '$lib/types/chatAttachment';
import { WEBUI_API_BASE_URL } from '$lib/constants';
import { centeredPopupFeatures } from './popup';
import type { LiveToolState } from './toolState';
import { writable } from 'svelte/store';
import {
	listConnections,
	listLiveGrants,
	enableLiveFamily,
	createConnection,
	authorizeConnection,
	getConnection,
	CloudSyncError
} from '$lib/apis/cloudSync';
import {
	connectResult,
	trustedConnectOrigins,
	connectionOutcome
} from '$lib/components/workspace/Knowledge/utils/cloudSync';

export const consentLabels: Record<string, string> = {
	onedrive: 'connect_provider_onedrive',
	outlook_mail: 'connect_provider_outlook_mail',
	google_drive: 'connect_provider_google_drive',
	confluence: 'connect_provider_confluence'
};

// soev-api refuses a subject without a proven identity link; a fresh Microsoft login creates it.
export const SUBJECT_NOT_LINKED_PROMPT = 'Log in again with Microsoft to connect';

/** The i18n key to show instead of a generic error when the account needs a new login. */
export function relinkPrompt(error: unknown): string | null {
	const code =
		error instanceof CloudSyncError ? error.code : error instanceof Error ? error.message : null;
	return code === 'subject_not_linked' ? SUBJECT_NOT_LINKED_PROMPT : null;
}

const ATTACH_ERRORS: Record<string, string> = {
	too_large: 'This file is too large to attach.',
	not_readable: 'This file cannot be read. It may be encrypted or protected.',
	not_found: 'This file is no longer available.',
	changed: 'This file changed while attaching. Try again.',
	connection_required: 'Reconnect OneDrive and try again.',
	reauth_required: 'Reconnect OneDrive and try again.',
	grant_disabled: 'Attaching this file is not allowed.',
	op_not_granted: 'Attaching this file is not allowed.',
	out_of_scope: 'Attaching this file is not allowed.',
	policy_forbids: 'Attaching this file is not allowed.',
	scope_insufficient: 'Attaching this file is not allowed.',
	budget_spent: 'Too many files at once. Try again in a minute.',
	provider_throttled: 'Too many files at once. Try again in a minute.',
	file_limit_exceeded: 'You have reached the maximum number of files.',
	soev_not_configured: 'Attaching OneDrive files is unavailable right now.',
	service_unavailable: 'Attaching OneDrive files is unavailable right now.'
};

/** The i18n key for a failed picker attach; unknown codes fall back to a generic message. */
export function attachErrorMessage(error: unknown): string {
	const code = error instanceof Error ? error.message : '';
	return relinkPrompt(error) ?? ATTACH_ERRORS[code] ?? 'Could not attach OneDrive file';
}

export type LiveConnection = {
	connection: Awaited<ReturnType<typeof listConnections>>[number];
	grantId?: string;
};
export type LiveFamily = 'live_documents' | 'mail';
/** The provider whose connection carries each family's grant (separate consent per family). */
export const LIVE_PROVIDERS: Record<LiveFamily, string> = {
	live_documents: 'onedrive',
	mail: 'outlook_mail'
};
let session: string | undefined;
const snapshots = new Map<string, LiveConnection[]>();
function snapshotKey(token: string, provider: string, family: LiveFamily): string {
	if (session !== token) {
		snapshots.clear();
		liveGrants.set({});
		session = token;
	}
	return `${provider}:${family}`;
}

/**
 * [Gradient] Per `provider:family`, whether the latest snapshot holds an enabled grant; absent
 * until the first snapshot. A family is only ever shown on (and sent on) with a grant.
 */
export const liveGrants = writable<Record<string, boolean>>({});
function remember(key: string, rows: LiveConnection[]) {
	snapshots.set(key, rows);
	const granted = rows.some((row) => !!row.grantId);
	liveGrants.update((grants) => (grants[key] === granted ? grants : { ...grants, [key]: granted }));
}

/** Whether `family` has an enabled grant: undefined while its snapshot is loading. */
export function liveGranted(
	grants: Record<string, boolean>,
	family: LiveFamily
): boolean | undefined {
	return grants[`${LIVE_PROVIDERS[family]}:${family}`];
}

/** A new chat starts a family on only when the tenant offers it and the user holds a grant. */
export function liveDefaultState(featureOn: boolean, granted: boolean | undefined): LiveToolState {
	return featureOn && granted === true ? 'auto' : 'off';
}

/** The state a turn sends: a family known to have no grant is off whatever was chosen. */
export function grantedLiveState(
	state: LiveToolState,
	granted: boolean | undefined
): LiveToolState {
	return granted === false ? 'off' : state;
}

/**
 * [Gradient] A connect finished outside the composer (the chat's connect card): the chat turns
 * the family on. `seq` makes every announcement distinct.
 */
export const liveConnected = writable<{ family: LiveFamily; seq: number } | null>(null);
let connectedSeq = 0;
export function announceLiveConnected(family: LiveFamily) {
	liveConnected.set({ family, seq: ++connectedSeq });
}

/** Prefetch the snapshot of every live family the tenant offers. */
export function prefetchLiveFamilies(token: string, families: LiveFamily[]) {
	for (const family of families)
		void prefetchLiveConnections(token, LIVE_PROVIDERS[family], family).catch(() => {});
}

// The composer prefetches every provider/family pair at once; they share one connection list.
let listing: { token: string; request: ReturnType<typeof listConnections> } | null = null;
function sharedConnections(token: string): ReturnType<typeof listConnections> {
	if (listing?.token !== token) {
		const request = listConnections(token).finally(() => {
			if (listing?.request === request) listing = null;
		});
		listing = { token, request };
	}
	return listing.request;
}

export async function prefetchLiveConnections(
	token: string,
	provider = 'onedrive',
	family: LiveFamily = 'live_documents'
): Promise<LiveConnection[]> {
	const key = snapshotKey(token, provider, family);
	const connections = (await sharedConnections(token)).filter(
		(c) => c.source_kind === provider && c.lifecycle !== 'revoked'
	);
	const rows = await Promise.all(
		connections.map(async (connection) => ({
			connection,
			grantId:
				connection.lifecycle === 'enabled' && !connection.last_error
					? (await listLiveGrants(token, connection.id, family)).find(
							(g) => g.lifecycle === 'enabled'
						)?.id
					: undefined
		}))
	);
	if (session === token) remember(key, rows);
	return rows;
}

export function liveConnections(
	token: string,
	provider = 'onedrive',
	family: LiveFamily = 'live_documents'
): LiveConnection[] | undefined {
	return snapshots.get(snapshotKey(token, provider, family));
}

export async function connectLiveSource(
	token: string,
	provider = 'onedrive',
	family: LiveFamily = 'live_documents',
	onConsentOpened?: () => void
): Promise<string> {
	if (!consentLabels[provider]) throw new Error('Unsupported connection provider');
	const rows = liveConnections(token, provider, family);
	if (!rows) {
		void prefetchLiveConnections(token, provider, family);
		throw new Error('Connection status is loading. Try again.');
	}
	const selected =
		rows.find((row) => row.connection.lifecycle === 'enabled' && !row.connection.last_error) ??
		rows[0];
	const connection = selected?.connection;
	if (connection?.lifecycle === 'enabled' && !connection.last_error) {
		if (selected.grantId) return selected.grantId;
		try {
			const grant = await enableLiveFamily(token, connection.id, family);
			selected.grantId = grant.id;
			remember(snapshotKey(token, provider, family), rows);
			return grant.id;
		} catch (error) {
			if (
				error instanceof CloudSyncError &&
				(error.code === 'reauth_required' ||
					(error.code === 'policy_forbids' && error.message.includes('Missing provider scopes')))
			) {
				connection.last_error = 'reauth_required';
			}
			throw error;
		}
	}
	const popup = window.open('about:blank', `soev-live-${family}`, centeredPopupFeatures(600, 720));
	if (!popup) throw new Error('Allow popups to connect your account');
	onConsentOpened?.();
	try {
		// soev-api answers a create for a provider the user already holds with that connection;
		// a healthy one needs no consent (no authorize URL), only the family enabled on it.
		const authorization = connection
			? { ...(await authorizeConnection(token, connection.id)), connection_id: connection.id }
			: await createConnection(token, provider);
		const id = authorization.connection_id;
		if (authorization.authorize_url) {
			const authorizeUrl = authorization.authorize_url;
			const trusted = trustedConnectOrigins(window.location.origin, WEBUI_API_BASE_URL);
			await new Promise<void>((resolve, reject) => {
				let closing = false;
				const finish = (error?: Error) => {
					clearInterval(timer);
					window.removeEventListener('message', received);
					if (error) reject(error);
					else resolve();
				};
				const received = (event: MessageEvent) => {
					const result = connectResult(event, trusted, popup, id);
					if (result)
						finish(result === 'pending' ? undefined : new Error('Provider connection failed'));
				};
				// [Gradient] A popup can close before its message arrives (or without one) while the
				// exchange succeeded: ask the connection once before calling it cancelled.
				const closed = async () => {
					closing = true;
					try {
						const outcome = connectionOutcome(await getConnection(token, id), 0);
						if (outcome.status === 'done') return finish();
					} catch {
						// Unreadable: treated as cancelled below.
					}
					finish(new Error('Provider connection cancelled'));
				};
				const started = Date.now();
				const timer = setInterval(() => {
					if (closing) return;
					if (Date.now() - started > 120000) finish(new Error('Provider connection cancelled'));
					else if (popup.closed) void closed();
				}, 500);
				window.addEventListener('message', received);
				popup.location.href = authorizeUrl;
			});
		}
		const started = Date.now();
		while (Date.now() - started <= 120000) {
			const outcome = connectionOutcome(await getConnection(token, id), Date.now() - started);
			if (outcome.status === 'done') return (await enableLiveFamily(token, id, family)).id;
			if (outcome.status !== 'waiting') throw new Error('Provider connection failed');
			await new Promise((resolve) => setTimeout(resolve, 1000));
		}
		throw new Error('Provider connection failed');
	} finally {
		popup.close();
		// Also after a cancel, error or timeout: a retry then finds the connection this attempt
		// created instead of creating another one.
		await prefetchLiveConnections(token, provider, family).catch(() => {});
	}
}

export async function attachPickedDocument(
	token: string,
	grantId: string,
	reference: DocumentReference,
	requestId: string
): Promise<ChatAttachment> {
	const response = await fetch(`${WEBUI_API_BASE_URL}/files/onedrive/attach`, {
		method: 'POST',
		headers: {
			Authorization: `Bearer ${token}`,
			'Content-Type': 'application/json',
			'Idempotency-Key': requestId
		},
		body: JSON.stringify({ grant_id: grantId, ...reference })
	});
	if (!response.ok) {
		const problem = await response.json();
		throw new Error(problem.detail?.code ?? 'request_failed');
	}
	return response.json();
}

export function matchingPickerConnection(
	rows: LiveConnection[],
	account: {
		tenantId: string;
		localAccountId: string;
		username: string;
		idTokenClaims?: { oid?: string };
	}
): LiveConnection {
	const oid = account.idTokenClaims?.oid ?? account.localAccountId;
	const matches = rows.filter(
		({ connection }) =>
			connection.source_kind === 'onedrive' &&
			connection.lifecycle === 'enabled' &&
			!connection.last_error &&
			!!account.tenantId &&
			connection.provider_tenant_id?.toLowerCase() === account.tenantId.toLowerCase() &&
			!!oid &&
			connection.provider_identity?.toLowerCase() === `entra:user:${oid}`.toLowerCase()
	);
	if (matches.length !== 1)
		throw new Error(
			'The picker account does not match a connected OneDrive account. Connect that account and try again.'
		);
	return matches[0];
}
