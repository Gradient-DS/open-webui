import { expect, it } from 'vitest';
import { syncErrorMessage } from './syncStatus';

it.each([
	[
		'access_revoked',
		'This source was moved or removed, or you no longer have access. Its documents were removed from this knowledge base.'
	],
	['writer_revoked', 'You can no longer edit this knowledge base; syncing is paused'],
	['credential_unusable', 'Reconnect {{provider}} to resume syncing.'],
	['reauth_required', 'Reconnect {{provider}} to resume syncing.'],
	['refresh_rejected', 'Reconnect {{provider}} to resume syncing.'],
	['future_code', 'future_code'],
	[null, ''],
	[undefined, '']
])('maps sync error %s to readable text', (code, expected) => {
	expect(syncErrorMessage(code)).toBe(expected);
});

it.each(['access_revoked', 'reauth_required', 'refresh_rejected', null])(
	'offers reconnect without claiming documents were removed for %s on suspended:reauth',
	(code) => {
		expect(syncErrorMessage(code, { lifecycle: 'suspended:reauth' })).toBe(
			'Reconnect {{provider}} to resume syncing.'
		);
	}
);

it('explains source removal on an enabled connection without suggesting reconnect', () => {
	expect(syncErrorMessage('access_revoked', { lifecycle: 'enabled' })).toBe(
		'This source was moved or removed, or you no longer have access. Its documents were removed from this knowledge base.'
	);
});
