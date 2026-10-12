import { afterEach, expect, it, vi } from 'vitest';
import { documentPreviewThrottle } from './documentPreview';
afterEach(() => vi.useRealTimers());
it('renders immediately, coalesces deltas every 500ms and flushes completion immediately', () => {
	vi.useFakeTimers();
	const render = vi.fn();
	const preview = documentPreviewThrottle(render);
	preview.update('first', false);
	for (let i = 1; i <= 4; i++) {
		vi.advanceTimersByTime(100);
		preview.update(`delta ${i}`, false);
	}
	expect(render.mock.calls).toEqual([['first']]);
	vi.advanceTimersByTime(100);
	expect(render.mock.calls).toEqual([['first'], ['delta 4']]);
	preview.update('final', true);
	expect(render.mock.calls).toEqual([['first'], ['delta 4'], ['final']]);
	vi.advanceTimersByTime(1000);
	expect(render).toHaveBeenCalledTimes(3);
});
it('cancels a pending frame update on unmount', () => {
	vi.useFakeTimers();
	const render = vi.fn();
	const preview = documentPreviewThrottle(render);
	preview.update('first', false);
	preview.update('pending', false);
	preview.destroy();
	vi.runAllTimers();
	expect(render).toHaveBeenCalledTimes(1);
});
