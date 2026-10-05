import type { DocumentReference } from './onedrive-file-picker';
import type { ChatAttachment } from '$lib/types/chatAttachment';
import { WEBUI_API_BASE_URL } from '$lib/constants';
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

export type LiveConnection = {
	connection: Awaited<ReturnType<typeof listConnections>>[number];
	grantId?: string;
};
export type LiveFamily = 'live_documents' | 'mail';
let session: string | undefined;
const snapshots = new Map<string, LiveConnection[]>();
function snapshotKey(token: string, provider: string, family: LiveFamily): string {
	if (session !== token) {
		snapshots.clear();
		session = token;
	}
	return `${provider}:${family}`;
}

export async function prefetchLiveConnections(
	token: string,
	provider = 'onedrive',
	family: LiveFamily = 'live_documents'
): Promise<LiveConnection[]> {
	const key = snapshotKey(token, provider, family);
	const connections = (await listConnections(token)).filter(
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
	if (session === token) snapshots.set(key, rows);
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
	family: LiveFamily = 'live_documents'
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
	const popup = window.open('about:blank', `soev-live-${family}`, 'width=600,height=720');
	if (!popup) throw new Error('Allow popups to connect your account');
	try {
		const authorization = connection
			? { ...(await authorizeConnection(token, connection.id)), connection_id: connection.id }
			: await createConnection(token, provider);
		const id = authorization.connection_id;
		const trusted = trustedConnectOrigins(window.location.origin, WEBUI_API_BASE_URL);
		await new Promise<void>((resolve, reject) => {
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
			const started = Date.now();
			const timer = setInterval(() => {
				if (popup.closed || Date.now() - started > 120000)
					finish(new Error('Provider connection cancelled'));
			}, 500);
			window.addEventListener('message', received);
			popup.location.href = authorization.authorize_url;
		});
		const started = Date.now();
		while (Date.now() - started <= 120000) {
			const outcome = connectionOutcome(await getConnection(token, id), Date.now() - started);
			if (outcome.status === 'done') {
				const grant = await enableLiveFamily(token, id, family);
				await prefetchLiveConnections(token, provider, family);
				return grant.id;
			}
			if (outcome.status !== 'waiting') throw new Error('Provider connection failed');
			await new Promise((resolve) => setTimeout(resolve, 1000));
		}
		throw new Error('Provider connection failed');
	} finally {
		popup.close();
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
