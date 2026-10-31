export type AuthPageState = 'loading' | 'redirecting' | 'form';

type AuthPageSession<T> = {
	hasSession: boolean;
	logout: boolean;
	cookie: string;
	redirect: () => Promise<void>;
	getSessionUser: (token: string) => Promise<T | null>;
	setSessionUser: (user: T, token: string) => Promise<void>;
	onError: (error: unknown) => void;
};

export async function resolveAuthPageState<T>({
	hasSession,
	logout,
	cookie,
	redirect,
	getSessionUser,
	setSessionUser,
	onError
}: AuthPageSession<T>): Promise<Exclude<AuthPageState, 'loading'>> {
	// null and undefined user stores are both signed out, never redirect candidates.
	if (hasSession && !logout) {
		await redirect();
		return 'redirecting';
	}

	const match = cookie.match(/(?:^|;\s*)token=([^;]*)/);
	const token = match ? decodeURIComponent(match[1]) : null;
	if (!token) return 'form';

	const sessionUser = await getSessionUser(token).catch((error: unknown) => {
		onError(error);
		return null;
	});
	if (!sessionUser) return 'form';

	// Session setup includes config refresh and navigation. Neither may expose the form.
	await setSessionUser(sessionUser, token);
	return 'redirecting';
}
