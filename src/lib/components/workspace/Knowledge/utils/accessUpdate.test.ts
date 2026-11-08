import { describe, expect, it } from 'vitest';
import { accessUpdateState } from './accessUpdate';

describe('applied knowledge access status', () => {
	it.each(['QUEUED', 'RUNNING'])('keeps %s pending until the stored job settles', (status) => {
		expect(accessUpdateState({ soev_access_update: { status } })).toBe('pending');
	});
	it.each(['FAILED', 'CANCELLED', 'COMPLETED_WITH_ERRORS'])(
		'shows %s as a failure rather than applied access',
		(status) => {
			expect(accessUpdateState({ soev_access_update: { status } })).toBe('failed');
		}
	);
	it.each([undefined, {}, { soev_access_update: { status: 'SUCCEEDED' } }])(
		'has no pending indication after success or without an owner status marker',
		(meta) => {
			expect(accessUpdateState(meta)).toBeUndefined();
		}
	);
});
