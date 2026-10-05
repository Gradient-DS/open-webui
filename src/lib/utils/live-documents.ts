import type { DocumentReference } from './onedrive-file-picker';
import type { ChatAttachment } from '$lib/types/chatAttachment';
import { WEBUI_API_BASE_URL } from '$lib/constants';
import {
	listConnections,
	listLiveDocumentGrants,
	enableLiveDocuments,
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
	google_drive: 'connect_provider_google_drive',
	confluence: 'connect_provider_confluence'
};

export type LiveDocumentConnection = {
	connection: Awaited<ReturnType<typeof listConnections>>[number];
	grantId?: string;
};
let snapshot:
	| { token: string; provider: string; connections: LiveDocumentConnection[] }
	| undefined;

export async function prefetchLiveDocuments(
	token: string,
	provider = 'onedrive'
): Promise<LiveDocumentConnection[]> {
	const connections = (await listConnections(token)).filter(
		(c) => c.source_kind === provider && c.lifecycle !== 'revoked'
	);
	const rows = await Promise.all(
		connections.map(async (connection) => ({
			connection,
			grantId:
				connection.lifecycle === 'enabled' && !connection.last_error
					? (await listLiveDocumentGrants(token, connection.id)).find(
							(g) => g.lifecycle === 'enabled'
						)?.id
					: undefined
		}))
	);
	snapshot = { token, provider, connections: rows };
	return rows;
}

export function liveDocumentConnections(
	token: string,
	provider = 'onedrive'
): LiveDocumentConnection[] | undefined {
	return snapshot?.token === token && snapshot.provider === provider
		? snapshot.connections
		: undefined;
}

export async function connectLiveDocuments(token: string, provider = 'onedrive'): Promise<string> {
	if (!consentLabels[provider]) throw new Error('Unsupported connection provider');
	const rows = liveDocumentConnections(token, provider);
	if (!rows) {
		void prefetchLiveDocuments(token, provider);
		throw new Error('Connection status is loading. Try again.');
	}
	const selected =
		rows.find((row) => row.connection.lifecycle === 'enabled' && !row.connection.last_error) ??
		rows[0];
	const connection = selected?.connection;
	if (connection?.lifecycle === 'enabled' && !connection.last_error) {
		if (selected.grantId) return selected.grantId;
		try {
			const grant = await enableLiveDocuments(token, connection.id);
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
	const popup = window.open('about:blank', 'soev-live-documents', 'width=600,height=720');
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
				const grant = await enableLiveDocuments(token, id);
				await prefetchLiveDocuments(token, provider);
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
	rows: LiveDocumentConnection[],
	account: {
		tenantId: string;
		localAccountId: string;
		username: string;
		idTokenClaims?: { oid?: string };
	}
): LiveDocumentConnection {
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
