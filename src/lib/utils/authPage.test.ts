import { describe, expect, it, vi } from 'vitest';
import { resolveAuthPageState, type AuthPageState } from './authPage';

const session = { id: 'user-1', token: 'test-token' };

function deferred<T>() {
	let resolve!: (value: T) => void;
	const promise = new Promise<T>((done) => {
		resolve = done;
	});
	return { promise, resolve };
}

function actions() {
	return {
		hasSession: false,
		logout: false,
		cookie: '',
		redirect: vi.fn(async () => {}),
		getSessionUser: vi.fn(async () => session),
		setSessionUser: vi.fn(async () => {}),
		onError: vi.fn()
	};
}

describe('/auth session state', () => {
	it.each([undefined, null])('shows the form for a signed-out %s user', async (user) => {
		const callbacks = { ...actions(), hasSession: Boolean(user) };
		expect(await resolveAuthPageState(callbacks)).toBe('form');
		expect(callbacks.redirect).not.toHaveBeenCalled();
		expect(callbacks.getSessionUser).not.toHaveBeenCalled();
	});

	it('keeps loading until existing-session navigation finishes, then never shows the form', async () => {
		const navigation = deferred<void>();
		const callbacks = {
			...actions(),
			hasSession: true,
			redirect: vi.fn(() => navigation.promise)
		};
		let state: AuthPageState = 'loading';
		const pending = resolveAuthPageState(callbacks).then((next) => {
			state = next;
		});
		await Promise.resolve();
		expect(state).toBe('loading');
		expect(callbacks.getSessionUser).not.toHaveBeenCalled();
		navigation.resolve();
		await pending;
		expect(state).toBe('redirecting');
	});

	it('keeps loading through OAuth verification, session setup and navigation', async () => {
		const verification = deferred<typeof session>();
		const navigation = deferred<void>();
		const callbacks = {
			...actions(),
			cookie: 'theme=dark;token=test%2Dtoken; other=value',
			getSessionUser: vi.fn(() => verification.promise),
			setSessionUser: vi.fn(() => navigation.promise)
		};
		let state: AuthPageState = 'loading';
		const pending = resolveAuthPageState(callbacks).then((next) => {
			state = next;
		});
		expect(callbacks.getSessionUser).toHaveBeenCalledWith('test-token');
		expect(state).toBe('loading');
		verification.resolve(session);
		await vi.waitFor(() =>
			expect(callbacks.setSessionUser).toHaveBeenCalledWith(session, 'test-token')
		);
		expect(state).toBe('loading');
		navigation.resolve();
		await pending;
		expect(state).toBe('redirecting');
		expect(callbacks.redirect).not.toHaveBeenCalled();
	});

	it.each(['', 'token=', 'oauth_id_token=other; nottoken=value'])(
		'does not attempt a callback without a token cookie (%s)',
		async (cookie) => {
			const callbacks = { ...actions(), cookie };
			expect(await resolveAuthPageState(callbacks)).toBe('form');
			expect(callbacks.getSessionUser).not.toHaveBeenCalled();
		}
	);

	it('allows retrying sign-in after a rejected OAuth session', async () => {
		const callbacks = { ...actions(), cookie: 'token=expired' };
		callbacks.getSessionUser.mockRejectedValueOnce('Invalid token');
		expect(await resolveAuthPageState(callbacks)).toBe('form');
		expect(callbacks.onError).toHaveBeenCalledWith('Invalid token');
		expect(callbacks.setSessionUser).not.toHaveBeenCalled();
	});

	it('allows retrying sign-in when the session endpoint returns no user', async () => {
		const callbacks = {
			...actions(),
			cookie: 'token=expired',
			getSessionUser: vi.fn(async () => null)
		};
		expect(await resolveAuthPageState(callbacks)).toBe('form');
		expect(callbacks.setSessionUser).not.toHaveBeenCalled();
	});

	it('honors the logout landing instead of redirecting an existing user', async () => {
		const callbacks = { ...actions(), hasSession: true, logout: true };
		expect(await resolveAuthPageState(callbacks)).toBe('form');
		expect(callbacks.redirect).not.toHaveBeenCalled();
	});

	it('does not fall back to the form when authenticated navigation fails', async () => {
		const callbacks = { ...actions(), cookie: 'token=test-token' };
		callbacks.setSessionUser.mockRejectedValueOnce(new Error('Navigation failed'));
		await expect(resolveAuthPageState(callbacks)).rejects.toThrow('Navigation failed');
		expect(callbacks.onError).not.toHaveBeenCalled();
	});
});
