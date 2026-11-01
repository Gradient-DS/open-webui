import { describe, expect, it, vi } from 'vitest';
import { CloudSyncError } from '$lib/apis/cloudSync';
import { addWithReconsent, mailboxErrorMessage } from './shared-mailboxes';

const refusal = (code: string, constraint?: string) =>
	new CloudSyncError(409, code, 'Untrusted upstream detail', constraint);

describe('shared mailbox refusals', () => {
	it.each([
		['mailbox_access_denied', 'This mailbox does not exist or you do not have access in Outlook.'],
		['reconsent_required', 'Reconnect Outlook to read shared mailboxes, then try again.'],
		['mailbox_limit', 'You can add up to 10 shared mailboxes. Remove one first.'],
		['connection_required', 'Turn on Mail search to connect Outlook, then try again.'],
		['invalid_field', 'Enter a valid email address for a shared mailbox.'],
		['policy_forbids', 'Shared mailboxes are not available in this workspace.']
	])('gives %s a fixed, translatable message', (code, message) => {
		expect(mailboxErrorMessage(refusal(code))).toBe(message);
	});
	it('explains that the own mailbox is already searched', () => {
		expect(mailboxErrorMessage(refusal('invalid_field', 'address:own_mailbox'))).toBe(
			'Your own mailbox is already included in Mail search.'
		);
	});
	it('does not display unknown upstream or exception details', () => {
		for (const error of [refusal('unknown_code'), new Error('private detail'), null])
			expect(mailboxErrorMessage(error)).toBe('Could not update shared mailboxes. Try again.');
	});
});

describe('adding with renewed consent', () => {
	const mailbox = { address: 'gedeeld@example.test', added_at: '2026-10-31T12:00:00Z' };
	it('saves normally without reauthorizing', async () => {
		const add = vi.fn().mockResolvedValue(mailbox);
		const reauthorize = vi.fn();
		expect(await addWithReconsent(add, reauthorize)).toEqual(mailbox);
		expect(add).toHaveBeenCalledOnce();
		expect(reauthorize).not.toHaveBeenCalled();
	});
	it('waits for renewed consent before retrying the add once', async () => {
		const events: string[] = [];
		const add = vi.fn(async () => {
			events.push('add');
			if (events.length === 1) throw refusal('reconsent_required');
			return mailbox;
		});
		const reauthorize = vi.fn(async () => {
			events.push('reauthorize');
		});
		expect(await addWithReconsent(add, reauthorize)).toEqual(mailbox);
		expect(events).toEqual(['add', 'reauthorize', 'add']);
	});
	it.each([
		'mailbox_access_denied',
		'mailbox_limit',
		'connection_required',
		'invalid_field',
		'policy_forbids'
	])('does not reauthorize or retry %s', async (code) => {
		const error = refusal(code);
		const add = vi.fn().mockRejectedValue(error);
		const reauthorize = vi.fn();
		await expect(addWithReconsent(add, reauthorize)).rejects.toBe(error);
		expect(add).toHaveBeenCalledOnce();
		expect(reauthorize).not.toHaveBeenCalled();
	});
	it('stops if renewed consent still does not permit shared mailboxes', async () => {
		const error = refusal('reconsent_required');
		const add = vi.fn().mockRejectedValue(error);
		const reauthorize = vi.fn().mockResolvedValue(undefined);
		await expect(addWithReconsent(add, reauthorize)).rejects.toBe(error);
		expect(add).toHaveBeenCalledTimes(2);
		expect(reauthorize).toHaveBeenCalledOnce();
	});
	it('does not retry after cancelled or failed consent', async () => {
		const add = vi.fn().mockRejectedValue(refusal('reconsent_required'));
		const cancelled = new Error('Provider connection cancelled');
		const reauthorize = vi.fn().mockRejectedValue(cancelled);
		await expect(addWithReconsent(add, reauthorize)).rejects.toBe(cancelled);
		expect(add).toHaveBeenCalledOnce();
	});
});
