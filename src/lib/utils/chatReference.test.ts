import { describe, expect, it } from 'vitest';
import { chatReference } from './chatReference';

describe('chatReference', () => {
	it('keeps legacy chat selections unchanged', () => {
		const item = { id: 'fictional-chat', title: 'Fictional title', updated_at: 42 };
		expect(chatReference(item)).toEqual(item);
	});

	it('uses the organization id for an owned v2 chat and retains its thread id', () => {
		expect(
			chatReference({
				thread_id: 'fictional-thread',
				organization: { id: 'fictional-chat' },
				updated_at: '2026-10-25T12:00:00Z'
			})
		).toEqual({
			id: 'fictional-chat',
			thread_id: 'fictional-thread',
			organization: { id: 'fictional-chat' },
			updated_at: 1792929600
		});
	});

	it('uses the thread id for a shared v2 chat without an organization', () => {
		expect(
			chatReference({ thread_id: 'fictional-shared-thread', organization: null, updated_at: 42 })
		).toEqual({
			id: 'fictional-shared-thread',
			thread_id: 'fictional-shared-thread',
			organization: null,
			updated_at: 42
		});
	});
});
