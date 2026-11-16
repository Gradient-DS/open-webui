import { afterEach, describe, expect, it, vi } from 'vitest';
import { expectSettled, markSettled, turnAnswered } from './turnSettled';

describe('expectSettled', () => {
	afterEach(() => {
		vi.useRealTimers();
	});

	it('resolves when the stopped message settles', async () => {
		const settled = expectSettled('m1', 1000);
		markSettled('m1');
		await expect(settled).resolves.toBe(true);
	});

	it('ignores other messages and gives up after the timeout', async () => {
		vi.useFakeTimers();
		const settled = expectSettled('m1', 1000);
		markSettled('m2');
		markSettled(null);
		vi.advanceTimersByTime(1000);
		await expect(settled).resolves.toBe(false);
	});

	it('does nothing for a message nobody waits on', () => {
		expect(() => markSettled('unknown')).not.toThrow();
	});
});

describe('turnAnswered', () => {
	const turn = (...answers: Array<{ id: string; done: boolean }>) => ({
		currentId: answers[0].id,
		messages: {
			u1: { role: 'user', childrenIds: answers.map((answer) => answer.id) },
			...Object.fromEntries(
				answers.map((answer) => [
					answer.id,
					{ role: 'assistant', parentId: 'u1', done: answer.done }
				])
			)
		}
	});

	it('holds while the answer streams', () => {
		expect(turnAnswered(turn({ id: 'a1', done: false }))).toBe(false);
	});

	it('releases once the answer is done', () => {
		expect(turnAnswered(turn({ id: 'a1', done: true }))).toBe(true);
	});

	it('holds while another model of the same turn still answers', () => {
		expect(turnAnswered(turn({ id: 'a1', done: true }, { id: 'a2', done: false }))).toBe(false);
	});

	it('holds when the turn has no answer yet', () => {
		expect(
			turnAnswered({ currentId: 'u1', messages: { u1: { role: 'user', childrenIds: [] } } })
		).toBe(false);
		expect(turnAnswered({ currentId: null, messages: {} })).toBe(false);
	});
});
