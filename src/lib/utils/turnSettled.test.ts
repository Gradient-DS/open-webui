import { afterEach, describe, expect, it, vi } from 'vitest';
import { expectSettled, markSettled } from './turnSettled';

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
