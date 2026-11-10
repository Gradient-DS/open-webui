import { readFileSync } from 'node:fs';
import ts from 'typescript';
import { expect, it, vi } from 'vitest';
import { createRawSnippet } from 'svelte';
import { render } from 'svelte/server';
import { writable } from 'svelte/store';
import SessionGate from './SessionGate.svelte';
import { createSessionBoundary } from '$lib/apis/session';
import { threadAPI } from '$lib/apis/threads';

vi.mock('$lib/apis/auths', () => ({ userSignOut: vi.fn(), getLogoutRedirectUrl: vi.fn() }));

const shell = vi.fn(() => '<aside>Sidebar and chat shell</aside>');
const show = (ready = false, denied = false) =>
	render(SessionGate, {
		props: { ready, denied, children: createRawSnippet(() => ({ render: shell })) },
		context: new Map([['i18n', writable({ t: (key: string) => key })]])
	}).body;

it.each([false, true])('does not render the shell while access is denied (ready=%s)', (ready) => {
	shell.mockClear();
	const html = show(ready, true);
	expect(html).toContain('You do not have access yet.');
	expect(html).toContain('Sign Out');
	expect(html).not.toContain('Sidebar and chat shell');
	expect(shell).not.toHaveBeenCalled();
});

// Exercise the root layout's access check and render condition without loading
// its socket, editor and desktop integrations.
const component = readFileSync('src/routes/+layout.svelte', 'utf8');
const script = ts.createSourceFile(
	'layout.js',
	component.split('</script>')[0].slice('<script>'.length),
	ts.ScriptTarget.Latest
);
const expressions = new Map<string, ts.Expression>();
const visit = (node: ts.Node) => {
	if (ts.isVariableDeclaration(node) && node.initializer)
		expressions.set(node.name.getText(script), node.initializer);
	if (ts.isBinaryExpression(node) && node.operatorToken.kind === ts.SyntaxKind.EqualsToken)
		expressions.set(node.left.getText(script), node.right);
	ts.forEachChild(node, visit);
};
visit(script);
const evaluate = (name: string, state: Record<string, any>) => {
	const expression = expressions.get(name);
	if (!expression) throw new Error(`Missing layout expression: ${name}`);
	return new Function('state', `with (state) { return (${expression.getText(script)}); }`)(state);
};
const fixture = () => {
	const state = {
		loaded: true,
		$sessionRedirecting: false,
		$config: { oauth: { required_provider: 'oidc' } },
		$user: { id: 'fern' },
		accessUser: 'fern',
		accessReady: false,
		localStorage: { token: 'session-token' },
		threadAPI,
		console: { error: vi.fn() },
		denied: false
	};
	return { state, html: () => show(evaluate('sessionReady', state), state.denied) };
};

it.each([200, 403, 404, 503])(
	'keeps the shell unmounted until access is granted (HTTP %s)',
	async (status) => {
		const f = fixture();
		let respond!: (response: Response) => void;
		const fetch = vi
			.fn<typeof globalThis.fetch>()
			.mockResolvedValue(new Response('{}'))
			.mockImplementationOnce(
				() =>
					new Promise((resolve) => {
						respond = resolve;
					})
			);
		const waitForRetry = vi.fn<() => Promise<void>>(() => new Promise(() => {}));
		const boundary = createSessionBoundary({
			fetch,
			origin: 'https://chat.example.invalid',
			apiBase: '/api/v1',
			token: () => 'session-token',
			expiresAt: () => undefined,
			onExpire: () => {
				f.state.$sessionRedirecting = true;
			},
			onNoAccess: () => {
				f.state.denied = true;
			},
			reload: vi.fn(),
			waitForRetry
		});
		vi.stubGlobal('fetch', boundary.fetch);
		try {
			const pending = evaluate('checkPlatformAccess', f.state)('fern', true);
			shell.mockClear();
			expect(f.html()).not.toContain('Sidebar and chat shell');
			expect(shell).not.toHaveBeenCalled();
			expect(fetch).toHaveBeenCalledOnce();
			respond(
				new Response(
					JSON.stringify(
						status === 200
							? { items: [] }
							: {
									state: status === 403 ? 'no_access' : status === 404 ? 'unavailable' : 'reconnect'
								}
					),
					{ status }
				)
			);
			if (status === 200) {
				await pending;
				expect(f.html()).toContain('Sidebar and chat shell');
			} else {
				if (status === 403) await vi.waitFor(() => expect(f.state.denied).toBe(true));
				else if (status === 503)
					await vi.waitFor(() => expect(waitForRetry).toHaveBeenCalledOnce());
				else await pending;
				expect(f.html()).not.toContain('Sidebar and chat shell');
				expect(shell).not.toHaveBeenCalled();
			}
		} finally {
			vi.unstubAllGlobals();
		}
	}
);

it('waits for authenticated config before starting the access check', () => {
	const f = fixture();
	const checkPlatformAccess = vi.fn();
	const state = { ...f.state, accessUser: null, checkPlatformAccess };
	const preflight = script.statements.find(
		(node) =>
			ts.isLabeledStatement(node) &&
			ts.isIfStatement(node.statement) &&
			node.statement.thenStatement.getText(script).includes('void checkPlatformAccess(')
	);
	if (!preflight || !ts.isLabeledStatement(preflight)) throw new Error('Missing access preflight');
	const run = () =>
		new Function('state', `with (state) { ${preflight.statement.getText(script)} }`)(state);
	run();
	expect(checkPlatformAccess).not.toHaveBeenCalled();
	expect(show(evaluate('sessionReady', state))).not.toContain('Sidebar and chat shell');
	state.$config = { ...state.$config, features: { enable_v2_threads: true } } as any;
	run();
	expect(checkPlatformAccess).toHaveBeenCalledExactlyOnceWith('fern', true);
	expect(show(evaluate('sessionReady', state))).not.toContain('Sidebar and chat shell');
});

it('keeps standalone sessions and the signed-out auth route available', () => {
	const f = fixture();
	f.state.$config.oauth.required_provider = '';
	expect(f.html()).toContain('Sidebar and chat shell');
	f.state.$config.oauth.required_provider = 'oidc';
	f.state.$user = undefined as any;
	expect(f.html()).toContain('Sidebar and chat shell');
});

it('does not reuse an access result for another user or render while the session is unknown', async () => {
	const f = fixture();
	f.state.threadAPI = () => ({ index: vi.fn().mockResolvedValue({ items: [] }) }) as any;
	f.state.accessUser = 'lark';
	await evaluate('checkPlatformAccess', f.state)('fern', true);
	expect(f.html()).not.toContain('Sidebar and chat shell');
	f.state.accessReady = true;
	f.state.accessUser = 'fern';
	f.state.loaded = false;
	expect(f.html()).not.toContain('Sidebar and chat shell');
});

it('gates both the desktop sidebar and route content in the root layout', () => {
	const gate = component.slice(
		component.indexOf('<SessionGate'),
		component.indexOf('</SessionGate>')
	);
	expect(gate).toContain('ready={sessionReady}');
	expect(gate).toContain('denied={noAccess && !$sessionRedirecting}');
	expect(gate).toContain('<AppSidebar />');
	expect(gate.match(/<slot \/>/g)).toHaveLength(2);
});
