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

export async function connectLiveDocuments(token: string): Promise<string> {
	const popup = window.open('about:blank', 'soev-live-documents', 'width=600,height=720');
	if (!popup) throw new Error('Allow popups to connect OneDrive');
	try {
		const connections = await listConnections(token);
		const connection = connections.find(
			(c) => c.source_kind === 'onedrive' && c.lifecycle !== 'revoked'
		);
		if (connection?.lifecycle === 'enabled') {
			const grants = await listLiveDocumentGrants(token, connection.id);
			const enabled = grants.find((g) => g.lifecycle === 'enabled');
			if (enabled) return enabled.id;
			try {
				return (await enableLiveDocuments(token, connection.id)).id;
			} catch (error) {
				if (
					!(error instanceof CloudSyncError) ||
					!(
						error.code === 'reauth_required' ||
						(error.code === 'policy_forbids' && error.message.includes('Missing provider scopes'))
					)
				)
					throw error;
			}
		}
		const authorization = connection
			? { ...(await authorizeConnection(token, connection.id)), connection_id: connection.id }
			: await createConnection(token, 'onedrive');
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
					finish(result === 'pending' ? undefined : new Error('OneDrive connection failed'));
			};
			const started = Date.now();
			const timer = setInterval(() => {
				if (popup.closed || Date.now() - started > 120000)
					finish(new Error('OneDrive connection cancelled'));
			}, 500);
			window.addEventListener('message', received);
			popup.location.href = authorization.authorize_url;
		});
		const started = Date.now();
		while (Date.now() - started <= 120000) {
			const outcome = connectionOutcome(await getConnection(token, id), Date.now() - started);
			if (outcome.status === 'done') return (await enableLiveDocuments(token, id)).id;
			if (outcome.status !== 'waiting') throw new Error('OneDrive connection failed');
			await new Promise((resolve) => setTimeout(resolve, 1000));
		}
		throw new Error('OneDrive connection failed');
	} finally {
		popup.close();
	}
}
