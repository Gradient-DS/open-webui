import { expect, it, vi } from 'vitest';
import { render } from 'svelte/server';
import { writable } from 'svelte/store';
import ThreadState from './ThreadState.svelte';

const show = (state: 'reconnect' | 'sign_in' | 'unavailable') =>
	render(ThreadState, {
		props: { state, retry: vi.fn() },
		context: new Map([['i18n', writable({ t: (key: string) => key })]])
	}).body;

it.each(['reconnect', 'sign_in'] as const)(
	'keeps %s out of individual chat views while shared recovery handles it',
	(state) => {
		const html = show(state);
		expect(html).not.toContain('<p');
		expect(html).not.toContain('<button');
	}
);

it('preserves unavailable-chat messages and their retry controls', () => {
	const html = show('unavailable');
	expect(html).toContain('This chat is unavailable');
	expect(html).toContain('Retry');
});
