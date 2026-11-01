import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';
import {
	connectionRetryCount,
	installSessionRecovery,
	recoverSession,
	registerConnectionRetry,
	retryConnection,
	waitForConnectionRetry
} from './session';

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
	retryConnection();
	vi.unstubAllGlobals();
	vi.useRealTimers();
});

it('combines reconnect states into one shared retry control and unregisters disposed views', () => {
	const first = vi.fn();
	const second = vi.fn();
	const removeFirst = registerConnectionRetry(first);
	const removeSecond = registerConnectionRetry(second);
	try {
		expect(get(connectionRetryCount)).toBe(2);
		retryConnection();
		expect(first).toHaveBeenCalledOnce();
		expect(second).toHaveBeenCalledOnce();
		removeFirst();
		retryConnection();
		expect(first).toHaveBeenCalledOnce();
		expect(second).toHaveBeenCalledTimes(2);
	} finally {
		removeFirst();
		removeSecond();
	}
	expect(get(connectionRetryCount)).toBe(0);
});

it('releases all waiting reads with one retry and removes cancelled reads', async () => {
	const controller = new AbortController();
	const aborted = waitForConnectionRetry(controller.signal, 2);
	const first = waitForConnectionRetry(undefined, 2);
	const second = waitForConnectionRetry(undefined, 2);
	expect(get(connectionRetryCount)).toBe(3);
	controller.abort();
	await expect(aborted).rejects.toBe(controller.signal.reason);
	expect(get(connectionRetryCount)).toBe(2);
	retryConnection();
	await Promise.all([first, second]);
	expect(get(connectionRetryCount)).toBe(0);
	expect(vi.getTimerCount()).toBe(0);
});

it.each([
	[1, 1000],
	[2, 2000],
	[3, 4000],
	[4, 8000],
	[5, 15000],
	[6, 15000]
])('automatically releases attempt %i after %i ms and cleans up', async (attempt, delay) => {
	const resumed = vi.fn();
	const waiting = waitForConnectionRetry(undefined, attempt).then(resumed);
	expect(get(connectionRetryCount)).toBe(attempt >= 2 ? 1 : 0);
	await vi.advanceTimersByTimeAsync(delay - 1);
	expect(resumed).not.toHaveBeenCalled();
	await vi.advanceTimersByTimeAsync(1);
	await waiting;
	expect(resumed).toHaveBeenCalledOnce();
	expect(get(connectionRetryCount)).toBe(0);
	expect(vi.getTimerCount()).toBe(0);
});

it('shows a wait once the request has been retrying for three seconds', async () => {
	const waiting = waitForConnectionRetry(undefined, 1, Date.now() - 2500);
	expect(get(connectionRetryCount)).toBe(0);
	await vi.advanceTimersByTimeAsync(499);
	expect(get(connectionRetryCount)).toBe(0);
	await vi.advanceTimersByTimeAsync(1);
	expect(get(connectionRetryCount)).toBe(1);
	await vi.advanceTimersByTimeAsync(500);
	await waiting;
	expect(get(connectionRetryCount)).toBe(0);
});

it('Retry also releases first failures whose banner is still hidden', async () => {
	const first = waitForConnectionRetry();
	const second = waitForConnectionRetry(undefined, 2);
	expect(get(connectionRetryCount)).toBe(1);
	retryConnection();
	await Promise.all([first, second]);
	expect(get(connectionRetryCount)).toBe(0);
	expect(vi.getTimerCount()).toBe(0);
});

it.each(['online', 'visible'] as const)('releases all waits early when %s', async (event) => {
	const browser = new EventTarget();
	const page = Object.assign(new EventTarget(), { visibilityState: 'hidden' });
	vi.stubGlobal('window', browser);
	vi.stubGlobal('document', page);
	const removeOnline = vi.spyOn(browser, 'removeEventListener');
	const removeVisible = vi.spyOn(page, 'removeEventListener');
	const resumed = vi.fn();
	const waits = [waitForConnectionRetry(), waitForConnectionRetry(undefined, 2)];
	const waiting = Promise.all(waits).then(resumed);
	page.dispatchEvent(new Event('visibilitychange'));
	await Promise.resolve();
	expect(resumed).not.toHaveBeenCalled();
	if (event === 'online') browser.dispatchEvent(new Event('online'));
	else {
		page.visibilityState = 'visible';
		page.dispatchEvent(new Event('visibilitychange'));
	}
	await waiting;
	expect(get(connectionRetryCount)).toBe(0);
	expect(vi.getTimerCount()).toBe(0);
	expect(removeOnline).toHaveBeenCalledTimes(2);
	expect(removeVisible).toHaveBeenCalledTimes(2);
});

it('does not leave a retry registration for an already cancelled read', async () => {
	const controller = new AbortController();
	controller.abort();
	await expect(waitForConnectionRetry(controller.signal)).rejects.toBe(controller.signal.reason);
	expect(get(connectionRetryCount)).toBe(0);
	expect(vi.getTimerCount()).toBe(0);
});

it('routes local ThreadErrors through the installed session handler and cleans it up', async () => {
	const recover = vi.fn(async () => {});
	const remove = installSessionRecovery(recover);
	await recoverSession('sign_in');
	await recoverSession('reconnect');
	expect(recover.mock.calls).toEqual([['sign_in'], ['reconnect']]);
	remove();
	await recoverSession('sign_in');
	expect(recover).toHaveBeenCalledTimes(2);
});
