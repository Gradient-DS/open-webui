import { describe, it, expect } from 'vitest';
import { temporaryChatId } from './index';

/**
 * Regression tests for GRA-221: every temporary chat in a browser session used
 * to reuse `local:<socket_id>` verbatim, so they all collapsed onto one agent
 * thread and inherited each other's history.
 *
 * The backend recovers the socket id from this id (`socket_id_from_chat_id` in
 * backend/open_webui/main.py) to authorise task list/stop on temporary chats,
 * so the socket id MUST stay the first segment.
 */
describe('temporaryChatId', () => {
	it('keeps the socket id as the first segment after the local: prefix', () => {
		const id = temporaryChatId('p555nxnIcxn8eLaHAAD1');

		expect(id.startsWith('local:')).toBe(true);
		expect(id.slice('local:'.length).split(':')[0]).toBe('p555nxnIcxn8eLaHAAD1');
	});

	it('is unique per call so each temporary chat gets its own agent thread', () => {
		const socketId = 'p555nxnIcxn8eLaHAAD1';

		const ids = new Set([
			temporaryChatId(socketId),
			temporaryChatId(socketId),
			temporaryChatId(socketId)
		]);

		expect(ids.size).toBe(3);
	});

	it('still yields a parseable id when the socket is not connected yet', () => {
		for (const missing of [undefined, null]) {
			const id = temporaryChatId(missing);

			expect(id.startsWith('local:')).toBe(true);
			// Empty first segment — the backend lookup simply finds no owner,
			// which is the same outcome as an unknown socket id.
			expect(id.slice('local:'.length).split(':')[0]).toBe('');
			expect(id.slice('local:'.length).split(':')[1]).toBeTruthy();
		}
	});
});
