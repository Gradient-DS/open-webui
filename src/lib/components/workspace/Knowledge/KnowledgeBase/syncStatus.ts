import type { Connection } from '$lib/apis/cloudSync';

export const SOURCE_UNAVAILABLE_MESSAGE =
	'This source was moved or removed, or you no longer have access. Its documents were removed from this knowledge base.';

const ERROR_MESSAGES: Record<string, string> = {
	access_revoked: SOURCE_UNAVAILABLE_MESSAGE,
	writer_revoked: 'You can no longer edit this knowledge base; syncing is paused',
	credential_unusable: 'Reconnect {{provider}} to resume syncing.',
	reauth_required: 'Reconnect {{provider}} to resume syncing.',
	refresh_rejected: 'Reconnect {{provider}} to resume syncing.'
};

export function syncErrorMessage(
	code: string | null | undefined,
	connection?: Pick<Connection, 'lifecycle'> | null
): string {
	if (connection?.lifecycle === 'suspended:reauth')
		return 'Reconnect {{provider}} to resume syncing.';
	return code ? (ERROR_MESSAGES[code] ?? code) : '';
}
