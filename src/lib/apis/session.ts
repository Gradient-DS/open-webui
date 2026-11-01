type SessionBoundaryOptions = {
	fetch: typeof fetch;
	origin: string;
	apiBase: string;
	token: () => string | null;
	expiresAt: () => number | undefined;
	onExpire: () => void;
	reload: () => void;
	waitForRetry: (
		signal?: AbortSignal | null,
		attempt?: number,
		startedAt?: number
	) => Promise<void>;
};

export const sessionLoginPath = (url: URL): string => {
	const returnTo =
		url.pathname === '/auth'
			? url.searchParams.get('redirect') || '/'
			: `${url.pathname}${url.search}${url.hash}`;
	return `/auth?${new URLSearchParams({ state: 'logout', redirect: returnTo })}`;
};

export const completeSessionSignOut = async (
	signOut: () => Promise<unknown>,
	navigate: () => void
) => {
	let timeout: ReturnType<typeof setTimeout> | undefined;
	try {
		await Promise.race([
			Promise.resolve().then(signOut),
			new Promise<void>((resolve) => (timeout = setTimeout(resolve, 1500)))
		]);
	} catch {
		// Local session removal is sufficient to reach login when server cleanup fails.
	} finally {
		clearTimeout(timeout);
		navigate();
	}
};

// Navigation replaces the document. Keep obsolete requests out of callers' catch handlers
// until unload, rather than manufacturing a successful response or an error toast.
const retiringRequest = new Promise<never>(() => {});

export const createSessionBoundary = (options: SessionBoundaryOptions) => {
	const api = new URL(options.apiBase, options.origin);
	let redirecting = false;
	let reloading = false;
	let probe: { token: string; at: number; result: Promise<boolean | null> } | undefined;
	const expire = () => {
		if (redirecting) return;
		redirecting = true;
		options.onExpire();
	};
	const expired = () => {
		const expiry = options.expiresAt();
		return expiry !== undefined && Date.now() >= expiry * 1000;
	};
	const unauthorized = (token: string) => {
		if (!probe || probe.token !== token || Date.now() - probe.at >= 1000) {
			const controller = new AbortController();
			const timeout = setTimeout(() => controller.abort(), 5000);
			probe = {
				token,
				at: Infinity,
				result: options
					.fetch(`${api.href}/auths/`, {
						headers: { Authorization: `Bearer ${token}` },
						credentials: 'include',
						cache: 'no-store',
						signal: controller.signal
					})
					.then((response) => (response.status === 401 ? true : response.ok ? false : null))
					.catch(() => null)
					.finally(() => {
						clearTimeout(timeout);
						if (probe?.token === token) probe.at = Date.now();
					})
			};
		}
		return probe.result;
	};
	const verify = async (token: string) => {
		const invalid = await unauthorized(token);
		if (options.token() === token && (invalid === true || expired())) expire();
		return invalid;
	};
	const recover = async (state: string) => {
		const token = options.token();
		if (!token || redirecting) return;
		if (state === 'sign_in' || expired()) {
			expire();
		} else if (state === 'reconnect') {
			await verify(token);
		}
	};
	const sessionFetch: typeof fetch = async (input, init) => {
		const url = new URL(input instanceof Request ? input.url : String(input), options.origin);
		const headers = new Headers(init?.headers ?? (input instanceof Request ? input.headers : {}));
		const token = options.token();
		const backend =
			url.origin === api.origin &&
			(url.pathname === api.pathname || url.pathname.startsWith(`${api.pathname}/`));
		if (backend && redirecting && headers.has('authorization')) return retiringRequest;
		const authenticated =
			backend && Boolean(token) && headers.get('authorization') === `Bearer ${token}`;
		if (!authenticated) return options.fetch(input, init);
		const current = () => !redirecting && options.token() === token;
		if (expired()) expire();
		if (!current()) return retiringRequest;
		const method = (
			init?.method ?? (input instanceof Request ? input.method : 'GET')
		).toUpperCase();
		// Only replay reads, including the read-only cache authorization POST.
		const retryable =
			method === 'GET' ||
			(method === 'POST' && url.pathname === `${api.pathname}/threads:authorize`);
		const signal = init?.signal ?? (input instanceof Request ? input.signal : undefined);
		let attempt = 0;
		let retryStartedAt: number | undefined;
		while (current()) {
			let response: Response | undefined;
			try {
				response = await options.fetch(input instanceof Request ? input.clone() : input, init);
			} catch (error) {
				if (!current()) return retiringRequest;
				if (signal?.aborted) throw error;
			}
			if (!current()) return retiringRequest;
			const payload =
				response && !response.ok
					? await response
							.clone()
							.json()
							.catch(() => null)
					: null;
			if (!current()) return retiringRequest;
			const state = payload?.state ?? payload?.detail?.state;
			let verificationUnavailable = false;
			if (
				state === 'sign_in' ||
				(response?.status === 401 && url.pathname === `${api.pathname}/auths/`)
			) {
				expire();
			} else if (
				!response ||
				response.status === 401 ||
				state === 'reconnect' ||
				[502, 503].includes(response.status)
			) {
				verificationUnavailable = (await verify(token!)) === null;
			}
			if (!current()) return retiringRequest;
			if (
				!response ||
				state === 'reconnect' ||
				[502, 503].includes(response.status) ||
				(response.status === 401 && verificationUnavailable)
			) {
				retryStartedAt ??= Date.now();
				attempt++;
				if (retryable) {
					console.warn('Session read retry', {
						method,
						path: url.pathname,
						status: response?.status ?? 'network error'
					});
				}
				try {
					await options.waitForRetry(signal, attempt, retryStartedAt);
				} catch (error) {
					if (!current()) return retiringRequest;
					throw error;
				}
				if (!current()) return retiringRequest;
				if (expired()) expire();
				if (!current()) return retiringRequest;
				if (!retryable) {
					// A write may have reached the server. Refresh its state instead of replaying it.
					if (!reloading) {
						reloading = true;
						options.reload();
					}
					return retiringRequest;
				}
				continue;
			}
			return response!;
		}
		return retiringRequest;
	};
	return { fetch: sessionFetch, expire, recover };
};
