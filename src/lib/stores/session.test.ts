import { expect, it, vi } from 'vitest';
import { get } from 'svelte/store';
import {
	connectionRetryCount,
	installSessionRecovery,
	recoverSession,
	registerConnectionRetry,
	retryConnection,
	waitForConnectionRetry
} from './session';

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
	const aborted = waitForConnectionRetry(controller.signal);
	const first = waitForConnectionRetry();
	const second = waitForConnectionRetry();
	expect(get(connectionRetryCount)).toBe(3);
	controller.abort();
	await expect(aborted).rejects.toBe(controller.signal.reason);
	expect(get(connectionRetryCount)).toBe(2);
	retryConnection();
	await Promise.all([first, second]);
	expect(get(connectionRetryCount)).toBe(0);
});

it('does not leave a retry registration for an already cancelled read', async () => {
	const controller = new AbortController();
	controller.abort();
	await expect(waitForConnectionRetry(controller.signal)).rejects.toBe(controller.signal.reason);
	expect(get(connectionRetryCount)).toBe(0);
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
