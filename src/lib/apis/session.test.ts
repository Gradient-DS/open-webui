import { afterEach, describe, expect, it, vi } from 'vitest';
import { threadAPI } from './threads';
import { completeSessionSignOut, createSessionBoundary, sessionLoginPath } from './session';

const json = (status: number, body: unknown = {}) => new Response(JSON.stringify(body), { status });
const deferred = <T>() => {
	let resolve!: (value: T) => void;
	const promise = new Promise<T>((yes) => (resolve = yes));
	return { promise, resolve };
};
const fixture = () => {
	const session = {
		token: 'session-token' as string | null,
		expiresAt: undefined as number | undefined
	};
	const fetch = vi.fn<typeof globalThis.fetch>();
	const waitForRetry = vi.fn<(signal?: AbortSignal | null) => Promise<void>>().mockResolvedValue();
	const reload = vi.fn();
	const onExpire = vi.fn(() => {
		session.token = null;
	});
	const boundary = createSessionBoundary({
		fetch,
		origin: 'https://webui.test',
		apiBase: '/api/v1',
		token: () => session.token,
		expiresAt: () => session.expiresAt,
		onExpire,
		reload,
		waitForRetry
	});
	const request = (path = '/chats/', init: RequestInit = {}) =>
		boundary.fetch(`/api/v1${path}`, {
			...init,
			headers: { Authorization: 'Bearer session-token' }
		});
	return { session, fetch, waitForRetry, reload, onExpire, boundary, request };
};

afterEach(() => {
	vi.unstubAllGlobals();
	vi.useRealTimers();
});

describe('session request boundary', () => {
	it('signs out once for concurrent projection sign-in failures without rejecting to callers', async () => {
		const f = fixture();
		f.fetch.mockImplementation(async () => json(401, { state: 'sign_in' }));
		vi.stubGlobal('fetch', f.boundary.fetch);
		const completed = vi.fn();
		const errorToast = vi.fn();
		for (let index = 0; index < 3; index++) {
			void threadAPI('session-token').projection(`chat-${index}`).then(completed, errorToast);
		}
		await vi.waitFor(() => expect(f.onExpire).toHaveBeenCalledOnce());
		f.boundary.expire();
		void f.request().then(completed, errorToast);
		void f.boundary
			.fetch('/api/v1/models', {
				headers: { Authorization: 'Bearer undefined' }
			})
			.then(completed, errorToast);
		await Promise.resolve();
		expect(f.onExpire).toHaveBeenCalledOnce();
		expect(f.fetch).toHaveBeenCalledTimes(3);
		expect(f.session.token).toBeNull();
		expect(completed).not.toHaveBeenCalled();
		expect(errorToast).not.toHaveBeenCalled();
	});

	it('accepts an explicit sign_in even when the OWUI session itself remains valid', async () => {
		const f = fixture();
		f.fetch.mockResolvedValue(json(503, { detail: { state: 'sign_in' } }));
		void f.request();
		await vi.waitFor(() => expect(f.onExpire).toHaveBeenCalledOnce());
		expect(f.fetch).toHaveBeenCalledOnce();
	});

	it('coalesces generic backend 401 checks and suppresses their errors after confirmed expiry', async () => {
		const f = fixture();
		const probe = deferred<Response>();
		f.fetch.mockImplementation(async (input) =>
			String(input).endsWith('/auths/') ? probe.promise : json(401, { detail: 'Invalid token' })
		);
		const rejected = vi.fn();
		for (let index = 0; index < 3; index++) void f.request().catch(rejected);
		await vi.waitFor(() => expect(f.fetch).toHaveBeenCalledTimes(4));
		probe.resolve(json(401));
		await vi.waitFor(() => expect(f.onExpire).toHaveBeenCalledOnce());
		expect(rejected).not.toHaveBeenCalled();
	});

	it('preserves a permission 401 when the session probe succeeds', async () => {
		const f = fixture();
		f.fetch.mockImplementation(async (input) =>
			String(input).endsWith('/auths/') ? json(200) : json(401, { detail: 'Access prohibited' })
		);
		expect((await f.request()).status).toBe(401);
		expect(f.onExpire).not.toHaveBeenCalled();
	});

	it('does not recursively probe the session endpoint', async () => {
		const f = fixture();
		f.fetch.mockResolvedValue(json(401));
		void f.request('/auths/');
		await vi.waitFor(() => expect(f.onExpire).toHaveBeenCalledOnce());
		expect(f.fetch).toHaveBeenCalledOnce();
	});

	it('keeps an unverified 401 behind Retry when the session verifier is unavailable', async () => {
		const f = fixture();
		const retry = deferred<void>();
		f.waitForRetry.mockReturnValue(retry.promise);
		f.fetch.mockResolvedValueOnce(json(401)).mockResolvedValueOnce(json(503));
		const errorToast = vi.fn();
		void f.request().catch(errorToast);
		await vi.waitFor(() => expect(f.waitForRetry).toHaveBeenCalledOnce());
		expect(f.onExpire).not.toHaveBeenCalled();
		expect(errorToast).not.toHaveBeenCalled();
		f.fetch.mockResolvedValueOnce(json(200));
		retry.resolve();
		await vi.waitFor(() => expect(f.fetch).toHaveBeenCalledTimes(3));
	});

	it.each([502, 503])(
		'verifies a reconnect %i and signs out if the probe returns 401',
		async (status) => {
			const f = fixture();
			f.fetch.mockImplementation(async (input) =>
				String(input).endsWith('/auths/') ? json(401) : json(status, { state: 'reconnect' })
			);
			void f.request();
			await vi.waitFor(() => expect(f.onExpire).toHaveBeenCalledOnce());
			expect(f.waitForRetry).not.toHaveBeenCalled();
		}
	);

	it('waits for one shared retry action and returns the eventual read without per-request failures', async () => {
		const f = fixture();
		const retry = deferred<void>();
		f.waitForRetry.mockReturnValue(retry.promise);
		let attempts = 0;
		f.fetch.mockImplementation(async (input) => {
			if (String(input).endsWith('/auths/')) return json(200);
			return ++attempts <= 3
				? new Response('Bad gateway', { status: 502 })
				: json(200, { items: [] });
		});
		const requests = [f.request(), f.request(), f.request()];
		const errors = vi.fn();
		for (const request of requests) void request.catch(errors);
		await vi.waitFor(() => expect(f.waitForRetry).toHaveBeenCalledTimes(3));
		expect(f.fetch).toHaveBeenCalledTimes(4);
		retry.resolve();
		expect((await Promise.all(requests)).map((response) => response.status)).toEqual([
			200, 200, 200
		]);
		expect(f.onExpire).not.toHaveBeenCalled();
		expect(errors).not.toHaveBeenCalled();
	});

	it('keeps a failed session probe as a retry state, and does not equate offline with logout', async () => {
		const f = fixture();
		f.fetch
			.mockRejectedValueOnce(new TypeError('Offline'))
			.mockRejectedValueOnce(new TypeError('Offline'))
			.mockResolvedValueOnce(json(200));
		expect((await f.request()).status).toBe(200);
		expect(f.waitForRetry).toHaveBeenCalledOnce();
		expect(f.onExpire).not.toHaveBeenCalled();
	});

	it('holds transient mutation failures and reloads once on Retry instead of replaying writes', async () => {
		const f = fixture();
		const retry = deferred<void>();
		f.waitForRetry.mockReturnValue(retry.promise);
		f.fetch.mockImplementation(async (input) =>
			String(input).endsWith('/auths/') ? json(200) : json(503, { state: 'reconnect' })
		);
		const completed = vi.fn();
		const errorToast = vi.fn();
		void f.request('/chats/', { method: 'POST', body: '{}' }).then(completed, errorToast);
		void f.request('/chats/thread', { method: 'DELETE' }).then(completed, errorToast);
		await vi.waitFor(() => expect(f.waitForRetry).toHaveBeenCalledTimes(2));
		expect(f.reload).not.toHaveBeenCalled();
		retry.resolve();
		await vi.waitFor(() => expect(f.reload).toHaveBeenCalledOnce());
		expect(f.fetch).toHaveBeenCalledTimes(3);
		expect(completed).not.toHaveBeenCalled();
		expect(errorToast).not.toHaveBeenCalled();
	});

	it('preserves Request headers and bodies across a read-only authorization retry', async () => {
		const f = fixture();
		f.fetch
			.mockResolvedValueOnce(json(503))
			.mockResolvedValueOnce(json(200))
			.mockResolvedValueOnce(json(200, { items: [] }));
		const request = new Request('https://webui.test/api/v1/threads:authorize', {
			method: 'POST',
			headers: { Authorization: 'Bearer session-token' },
			body: '{"threads":[]}'
		});
		expect((await f.boundary.fetch(request)).status).toBe(200);
		const original = f.fetch.mock.calls[0][0] as Request;
		const retried = f.fetch.mock.calls[2][0] as Request;
		expect(original.headers.get('authorization')).toBe('Bearer session-token');
		expect(await original.text()).toBe(await retried.text());
		expect(request.bodyUsed).toBe(false);
	});

	it('checks local expiry before sending a request or displaying a local ThreadError', async () => {
		const f = fixture();
		f.session.expiresAt = Date.now() / 1000 - 1;
		await f.boundary.recover('reconnect');
		void f.request();
		expect(f.onExpire).toHaveBeenCalledOnce();
		expect(f.fetch).not.toHaveBeenCalled();
	});

	it('does not sign out a replacement session when an old request or probe finishes', async () => {
		const f = fixture();
		const pending = deferred<Response>();
		f.fetch.mockReturnValueOnce(pending.promise);
		void f.request();
		f.session.token = 'replacement-token';
		pending.resolve(json(401, { state: 'sign_in' }));
		await Promise.resolve();
		await Promise.resolve();
		expect(f.onExpire).not.toHaveBeenCalled();
		const probe = deferred<Response>();
		f.fetch.mockReturnValueOnce(probe.promise);
		const recovering = f.boundary.recover('reconnect');
		f.session.token = 'third-token';
		probe.resolve(json(401));
		await recovering;
		expect(f.onExpire).not.toHaveBeenCalled();
	});

	it.each([
		['https://provider.test/api/v1/models', { Authorization: 'Bearer session-token' }],
		['/openai/models', { Authorization: 'Bearer session-token' }],
		['/api/v1/models', { Authorization: 'Bearer provider-key' }],
		['/api/v1/auths/signin', {}],
		['/api/v1/auths/signout', {}]
	])('leaves unrelated, unauthenticated, and login requests alone: %s', async (url, headers) => {
		const f = fixture();
		f.fetch.mockResolvedValue(json(401));
		expect((await f.boundary.fetch(url, { headers })).status).toBe(401);
		expect(f.onExpire).not.toHaveBeenCalled();
		expect(f.fetch).toHaveBeenCalledOnce();
	});

	it('honors init header overrides and request cancellation', async () => {
		const f = fixture();
		f.fetch.mockResolvedValueOnce(json(401));
		const request = new Request('https://webui.test/api/v1/chats/', {
			headers: { Authorization: 'Bearer session-token' }
		});
		expect((await f.boundary.fetch(request, { headers: {} })).status).toBe(401);
		const controller = new AbortController();
		controller.abort();
		f.fetch.mockRejectedValueOnce(controller.signal.reason);
		await expect(f.request('/chats/', { signal: controller.signal })).rejects.toBe(
			controller.signal.reason
		);
		expect(f.onExpire).not.toHaveBeenCalled();
		expect(f.waitForRetry).not.toHaveBeenCalled();
	});
});

describe('sign-out navigation', () => {
	it('keeps the path, query, and fragment and suppresses automatic SSO login', () => {
		const path = sessionLoginPath(new URL('https://webui.test/c/thread?model=one%2Ftwo#message-2'));
		const login = new URL(path, 'https://webui.test');
		expect(login.pathname).toBe('/auth');
		expect(login.searchParams.get('state')).toBe('logout');
		expect(login.searchParams.get('redirect')).toBe('/c/thread?model=one%2Ftwo#message-2');
		expect(new URL(sessionLoginPath(login), login).searchParams.get('redirect')).toBe(
			'/c/thread?model=one%2Ftwo#message-2'
		);
	});

	it.each(['resolved', 'rejected', 'hanging'] as const)(
		'navigates exactly once after %s server cleanup',
		async (mode) => {
			vi.useFakeTimers();
			const pending = deferred<void>();
			const signOut = vi.fn(() =>
				mode === 'hanging'
					? pending.promise
					: mode === 'rejected'
						? Promise.reject(new Error('Offline'))
						: Promise.resolve()
			);
			const navigate = vi.fn();
			const completion = completeSessionSignOut(signOut, navigate);
			await vi.advanceTimersByTimeAsync(1500);
			await completion;
			pending.resolve();
			await Promise.resolve();
			expect(signOut).toHaveBeenCalledOnce();
			expect(navigate).toHaveBeenCalledOnce();
		}
	);
});
