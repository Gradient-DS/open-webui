import { expect, it } from 'vitest';
import { syncErrorMessage } from './syncStatus';

it.each([
	[
		'access_revoked',
		'Access to the source was revoked; synced documents are hidden until you reconnect'
	],
	['writer_revoked', 'You can no longer edit this knowledge base; syncing is paused'],
	['credential_unusable', 'Reconnect {{provider}} to resume syncing.'],
	['future_code', 'future_code'],
	[null, ''],
	[undefined, '']
])('maps sync error %s to readable text', (code, expected) => {
	expect(syncErrorMessage(code)).toBe(expected);
});
