const ERROR_MESSAGES: Record<string, string> = {
	access_revoked:
		'Access to the source was revoked; synced documents are hidden until you reconnect',
	writer_revoked: 'You can no longer edit this knowledge base; syncing is paused',
	credential_unusable: 'Reconnect {{provider}} to resume syncing.'
};

export function syncErrorMessage(code: string | null | undefined): string {
	return code ? (ERROR_MESSAGES[code] ?? code) : '';
}
