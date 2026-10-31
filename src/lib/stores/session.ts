import { readonly, writable } from 'svelte/store';

export const sessionRedirecting = writable(false);
const retryCount = writable(0);
export const connectionRetryCount = readonly(retryCount);
const retries = new Set<() => unknown>();

export const registerConnectionRetry = (retry: () => unknown) => {
	retries.add(retry);
	retryCount.set(retries.size);
	return () => {
		retries.delete(retry);
		retryCount.set(retries.size);
	};
};

export const retryConnection = () => {
	for (const retry of [...retries]) retry();
};

export const waitForConnectionRetry = (signal?: AbortSignal | null): Promise<void> =>
	new Promise((resolve, reject) => {
		const remove = registerConnectionRetry(() => {
			cleanup();
			resolve();
		});
		const abort = () => {
			cleanup();
			reject(signal?.reason ?? new DOMException('Aborted', 'AbortError'));
		};
		const cleanup = () => {
			remove();
			signal?.removeEventListener('abort', abort);
		};
		if (signal?.aborted) abort();
		else signal?.addEventListener('abort', abort, { once: true });
	});

let recover: ((state: string) => Promise<void>) | undefined;
export const installSessionRecovery = (handler: NonNullable<typeof recover>) => {
	recover = handler;
	return () => {
		if (recover === handler) recover = undefined;
	};
};
export const recoverSession = (state: string) => recover?.(state);
