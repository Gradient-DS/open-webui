import { readFileSync } from 'node:fs';
import ts from 'typescript';
import equal from 'fast-deep-equal';
import { afterEach, expect, it, vi } from 'vitest';

// Exercise the component's callbacks without mounting its editor/media dependencies.
const source = readFileSync('src/lib/components/chat/Chat.svelte', 'utf8').split('</script>')[0];
const script = ts.createSourceFile(
	'Chat.ts',
	source.slice(source.indexOf('>') + 1),
	ts.ScriptTarget.Latest
);
function callback(name: string, state: Record<string, any>) {
	let expression: ts.Node | undefined;
	const visit = (node: ts.Node) => {
		if (
			(ts.isVariableDeclaration(node) || ts.isPropertyAssignment(node)) &&
			node.name.getText(script) === name
		)
			expression = node.initializer;
		ts.forEachChild(node, visit);
	};
	visit(script);
	if (!expression) throw new Error(`Missing callback: ${name}`);
	const js = ts.transpileModule(`const handler = ${expression.getText(script)};`, {
		compilerOptions: { target: ts.ScriptTarget.ESNext, module: ts.ModuleKind.None }
	}).outputText;
	return new Function('state', `with (state) { ${js}; return handler; }`)(state);
}

afterEach(() => vi.useRealTimers());

it('applies v2 tag events and refreshes global tags without blocking or replacing the chat', async () => {
	const chat = { id: 'thread' };
	let resolveTags!: (value: { name: string }[]) => void;
	const globalTags = new Promise<{ name: string }[]>((resolve) => {
		resolveTags = resolve;
	});
	const state = {
		$chatId: 'thread',
		$config: { features: { enable_v2_threads: true } },
		tick: vi.fn(),
		chat,
		tags: [],
		localStorage: { token: 'token' },
		allTags: { set: vi.fn() },
		getChatById: vi.fn(),
		getTagsById: vi.fn(),
		getAllTags: vi.fn(() => globalTags)
	};
	await callback(
		'chatEventHandler',
		state
	)({
		chat_id: 'thread',
		data: { type: 'chat:tags', data: ['grove'] }
	});
	expect(state.tags).toEqual([{ name: 'grove' }]);
	expect(state.chat).toBe(chat);
	for (const fetch of [state.getChatById, state.getTagsById]) expect(fetch).not.toHaveBeenCalled();
	expect(state.getAllTags).toHaveBeenCalledExactlyOnceWith('token');
	expect(state.allTags.set).not.toHaveBeenCalled();
	const tags = [{ name: 'grove' }, { name: 'existing' }];
	resolveTags(tags);
	await globalTags;
	expect(state.allTags.set).toHaveBeenCalledExactlyOnceWith(tags);
});

it.each([true, false])('preserves projection scroll when autoScroll=%s', async (autoScroll) => {
	vi.useFakeTimers();
	const projected = {
		currentId: 'answer',
		messages: { answer: { role: 'assistant', done: true } }
	};
	const container = { scrollTop: 250, scrollHeight: 2000 };
	const state: Record<string, any> = {
		$chatId: 'thread',
		$config: { features: { enable_v2_threads: true } },
		generationController: null,
		document: { visibilityState: 'visible' },
		threadRefreshTimer: undefined,
		threadPage: null,
		localStorage: { token: 'token' },
		getChatById: vi.fn().mockResolvedValue({ title: 'Grove', tags: [] }),
		buildThreadHistory: () => projected,
		history: { currentId: null, messages: {} },
		messagesContainerElement: container,
		autoScroll,
		followScrollTop: null,
		equal,
		tick: vi.fn(async () => {
			container.scrollTop = 0;
		}),
		chatTitle: { set: vi.fn() },
		tags: [],
		toast: { error: vi.fn() }
	};
	state.refreshThreadProjection = callback('refreshThreadProjection', state);
	state.refreshThreadProjection('thread');
	await vi.advanceTimersByTimeAsync(1000);
	expect(state.toast.error).not.toHaveBeenCalled();
	expect(container.scrollTop).toBe(autoScroll ? 2000 : 250);
	expect(state.autoScroll).toBe(autoScroll);
	expect(state.followScrollTop).toBe(container.scrollTop);
	// A metadata-only refresh retains history and message identity.
	const history = state.history;
	state.buildThreadHistory = () => structuredClone(projected);
	state.tick.mockClear();
	state.refreshThreadProjection('thread');
	await vi.advanceTimersByTimeAsync(1000);
	expect(state.history).toBe(history);
	expect(state.tick).not.toHaveBeenCalled();
});

it.each([true, false])(
	'refreshes a newly created chat before streaming, embedded=%s',
	async (embedded) => {
		const state = {
			$chatId: '',
			_chatId: '',
			history: { messages: { answer: {} } },
			responseMessageId: 'answer',
			controller: new AbortController(),
			chatRequestQueues: { update: vi.fn() },
			chatId: { set: vi.fn() },
			embedded,
			window: { history: { state: {}, replaceState: vi.fn() } },
			localStorage: { token: 'token' },
			$selectedFolder: { id: 'folder' },
			selectedFolder: { set: vi.fn() },
			// Never resolve: list reads must not block the callback or streaming.
			refreshChatList: vi.fn(() => new Promise(() => {})),
			refreshFolderChatLists: vi.fn(() => new Promise(() => {})),
			saveChatHandler: vi.fn()
		};
		await callback('created', state)('thread');
		expect(state.controller.signal.aborted).toBe(false);
		expect(state.refreshChatList).toHaveBeenCalledTimes(embedded ? 0 : 1);
		if (!embedded) expect(state.refreshChatList).toHaveBeenCalledWith('token');
		expect(state.refreshFolderChatLists).toHaveBeenCalledWith('folder');
		expect(state.selectedFolder.set).toHaveBeenCalledWith(null);
		expect(state.saveChatHandler).toHaveBeenCalledWith('thread', state.history);
	}
);
