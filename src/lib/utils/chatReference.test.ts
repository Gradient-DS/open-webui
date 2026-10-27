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
				owned: true,
				organization: { id: 'fictional-chat' },
				updated_at: '2026-10-25T12:00:00Z'
			})
		).toEqual({
			id: 'fictional-chat',
			thread_id: 'fictional-thread',
			owned: true,
			organization: { id: 'fictional-chat' },
			updated_at: 1792929600
		});
	});

	it('uses the thread id for a shared chat with a local record', () => {
		const item = {
			thread_id: 'fictional-shared-thread',
			owned: false,
			organization: { id: 'fictional-local-chat' },
			updated_at: 42
		};
		expect(chatReference(item).id).toBe(item.thread_id);
		expect(chatReference({ ...item, readonly: false }).id).toBe(item.organization.id);
	});

	it('uses the thread id for an owned chat without a local record', () => {
		const item = { thread_id: 'fictional-thread', owned: true, organization: null, updated_at: 42 };
		expect(chatReference(item).id).toBe(item.thread_id);
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
