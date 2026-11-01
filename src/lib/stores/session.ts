import { readonly, writable } from 'svelte/store';

export const sessionRedirecting = writable(false);
const retryCount = writable(0);
export const connectionRetryCount = readonly(retryCount);
const retries = new Map<() => unknown, boolean>();
const updateRetryCount = () => retryCount.set([...retries.values()].filter(Boolean).length);

export const registerConnectionRetry = (retry: () => unknown, showAfter = 0) => {
	retries.set(retry, showAfter === 0);
	updateRetryCount();
	const bannerTimeout =
		showAfter > 0
			? setTimeout(() => {
					retries.set(retry, true);
					updateRetryCount();
				}, showAfter)
			: undefined;
	return () => {
		clearTimeout(bannerTimeout);
		retries.delete(retry);
		updateRetryCount();
	};
};

export const retryConnection = () => {
	for (const retry of [...retries.keys()]) retry();
};

export const waitForConnectionRetry = (
	signal?: AbortSignal | null,
	attempt = 1,
	startedAt = Date.now()
): Promise<void> =>
	new Promise((resolve, reject) => {
		const resume = () => {
			cleanup();
			resolve();
		};
		const remove = registerConnectionRetry(
			resume,
			attempt >= 2 ? 0 : Math.max(0, 3000 - (Date.now() - startedAt))
		);
		const timeout = setTimeout(resume, Math.min(1000 * 2 ** (attempt - 1), 15000));
		const visible = () => {
			if (document.visibilityState === 'visible') resume();
		};
		const abort = () => {
			cleanup();
			reject(signal?.reason ?? new DOMException('Aborted', 'AbortError'));
		};
		const cleanup = () => {
			clearTimeout(timeout);
			remove();
			signal?.removeEventListener('abort', abort);
			if (typeof window !== 'undefined') window.removeEventListener('online', resume);
			if (typeof document !== 'undefined')
				document.removeEventListener('visibilitychange', visible);
		};
		if (signal?.aborted) abort();
		else {
			signal?.addEventListener('abort', abort, { once: true });
			if (typeof window !== 'undefined') window.addEventListener('online', resume);
			if (typeof document !== 'undefined') document.addEventListener('visibilitychange', visible);
		}
	});

let recover: ((state: string) => Promise<void>) | undefined;
export const installSessionRecovery = (handler: NonNullable<typeof recover>) => {
	recover = handler;
	return () => {
		if (recover === handler) recover = undefined;
	};
};
export const recoverSession = (state: string) => recover?.(state);
