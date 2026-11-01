import { CloudSyncError } from '$lib/apis/cloudSync';
import { relinkPrompt } from './live-connections';

const MAILBOX_ERRORS: Record<string, string> = {
	mailbox_access_denied: 'This mailbox does not exist or you do not have access in Outlook.',
	reconsent_required: 'Reconnect Outlook to read shared mailboxes, then try again.',
	mailbox_limit: 'You can add up to 10 shared mailboxes. Remove one first.',
	connection_required: 'Turn on Mail search to connect Outlook, then try again.',
	invalid_field: 'Enter a valid email address for a shared mailbox.',
	policy_forbids: 'Shared mailboxes are not available in this workspace.'
};

export function mailboxErrorMessage(error: unknown): string {
	if (error instanceof CloudSyncError) {
		if (error.code === 'invalid_field' && error.constraint === 'address:own_mailbox')
			return 'Your own mailbox is already included in Mail search.';
		return (
			relinkPrompt(error) ??
			MAILBOX_ERRORS[error.code] ??
			'Could not update shared mailboxes. Try again.'
		);
	}
	if (error instanceof Error) {
		if (error.message === 'Allow popups to connect your account') return error.message;
		if (error.message === 'Provider connection cancelled')
			return 'Outlook connection cancelled. Try again.';
		if (error.message === 'Provider connection failed')
			return 'Could not reconnect Outlook. Try again.';
	}
	return 'Could not update shared mailboxes. Try again.';
}

export async function addWithReconsent<T>(
	add: () => Promise<T>,
	reauthorize: () => Promise<void>
): Promise<T> {
	try {
		return await add();
	} catch (error) {
		if (!(error instanceof CloudSyncError) || error.code !== 'reconsent_required') throw error;
	}
	await reauthorize();
	return add();
}
