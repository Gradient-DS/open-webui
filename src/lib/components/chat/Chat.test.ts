import { readFileSync } from 'node:fs';
import ts from 'typescript';
import equal from 'fast-deep-equal';
import { afterEach, expect, it, vi } from 'vitest';
import { buildThreadHistory, extendProjectionToTurn, mergeProjection } from '$lib/utils/threads';
import type { ProjectionPage } from '$lib/apis/threads';
import { retainReasoningTiming, updateThreadReasoning } from '$lib/utils/threadReasoning';
import { canRegenerateResponse } from '$lib/utils/features';
import { threadEditInput, threadRegenerationInput } from '$lib/utils/threadRegeneration';

// Exercise the component's callbacks without mounting its editor/media dependencies.
const component = readFileSync('src/lib/components/chat/Chat.svelte', 'utf8');
const source = component.split('</script>')[0];
const script = ts.createSourceFile(
	'Chat.ts',
	source.slice(source.indexOf('>') + 1),
	ts.ScriptTarget.Latest
);
function callback(name: string, state: Record<string, any>) {
	state.threadVersionLoad ??= 0;
	state.loadThreadVersions ??= vi.fn().mockResolvedValue(undefined);
	state.threadVersions ??= [];
	state.threadPage ??= { thread_id: 'thread' };
	state.threadAPI ??= () => ({ versions: vi.fn().mockResolvedValue({ items: [] }) });
	state.switchingThread ??= false;
	state.$config ??= { features: {} };
	state.updateThreadReasoning ??= updateThreadReasoning;
	state.retainReasoningTiming ??= retainReasoningTiming;
	state.createMessagesList ??= (history: any) => Object.values(history.messages);
	state.updateLastReadAt ??= vi.fn();
	state.$temporaryChatEnabled ??= false;

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

it.each([true, false])('persists reads over HTTP only for v2=%s', async (v2) => {
	const state = {
		$config: { features: { enable_v2_threads: v2 } },
		localStorage: { token: 'token' },
		markConversationRead: vi.fn().mockResolvedValue({ chat_id: 'conversation', last_read_at: 1 }),
		$socket: { emit: vi.fn() }
	};
	callback('updateLastReadAt', state)('conversation');
	if (v2) {
		expect(state.markConversationRead).toHaveBeenCalledExactlyOnceWith('token', 'conversation');
		expect(state.$socket.emit).not.toHaveBeenCalled();
	} else {
		expect(state.markConversationRead).not.toHaveBeenCalled();
		expect(state.$socket.emit).toHaveBeenCalledWith('events:chat', {
			chat_id: 'conversation',
			data: { type: 'last_read_at' }
		});
	}
});

it.each([true, false])(
	'marks a completed answer read only when its conversation is open=%s',
	async (open) => {
		const message = { id: 'answer', content: 'Answer', model: 'model', done: false };
		const state = {
			$config: { features: { enable_v2_threads: true } },
			$chatId: open ? 'conversation' : 'other',
			$temporaryChatEnabled: false,
			$settings: {},
			$showCallOverlay: false,
			history: { currentId: 'answer', messages: { answer: message } },
			followStreamingResponse: vi.fn(),
			updateThreadReasoning: vi.fn(),
			getOutputText: () => '',
			removeAllDetails: (content: string) => content,
			dispatchCallOverlayAudio: vi.fn(),
			eventTarget: { dispatchEvent: vi.fn() },
			CustomEvent: class {
				constructor(
					public name: string,
					public options: unknown
				) {}
			},
			turnAnswered: () => true,
			taskIds: null,
			tick: vi.fn(),
			scheduleResponseScrollToBottom: vi.fn(),
			chatCompletedHandler: vi.fn(),
			processNextInQueue: vi.fn(),
			updateLastReadAt: vi.fn()
		};
		const handler = callback('chatCompletionEventHandler', state);
		await handler({ content: 'Answer' }, message, 'conversation');
		expect(state.updateLastReadAt).not.toHaveBeenCalled();
		await handler({ done: true }, message, 'conversation');
		expect(state.updateLastReadAt).toHaveBeenCalledTimes(open ? 1 : 0);
		if (open) expect(state.updateLastReadAt).toHaveBeenCalledWith('conversation');
	}
);

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

it.each([true, false])('preserves projection viewport when autoScroll=%s', async (autoScroll) => {
	vi.useFakeTimers();
	const projected = {
		currentId: 'server-answer',
		messages: {
			'server-user': { id: 'server-user', role: 'user', done: true },
			'server-answer': { id: 'server-answer', role: 'assistant', done: true }
		}
	};
	const previous = {
		currentId: 'local-answer',
		messages: {
			'local-user': { id: 'local-user', role: 'user' },
			'local-answer': { id: 'local-answer', role: 'assistant' }
		}
	};
	const container = {
		scrollTop: 250,
		scrollHeight: 2000,
		contains: () => true,
		getBoundingClientRect: () => ({ top: 20, bottom: 620 })
	};
	const state: Record<string, any> = {
		$chatId: 'thread',
		$config: { features: { enable_v2_threads: true } },
		generationController: null,
		document: {
			visibilityState: 'visible',
			getElementById: (id: string) => {
				if (!(id.slice('message-'.length) in state.history.messages)) return null;
				const user = id.endsWith('user');
				const top = user ? 0 : state.history === previous ? 200 : 500;
				return {
					getBoundingClientRect: () => ({
						top: 20 + top - container.scrollTop,
						bottom: 20 + top - container.scrollTop + (user ? 100 : 1500)
					})
				};
			}
		},
		threadRefreshTimer: undefined,
		threadPage: null,
		loadThreadProjection: vi.fn().mockResolvedValue({
			thread_id: 'thread',
			generation: 'generation',
			title: 'Grove',
			tags: [],
			messages: []
		}),
		buildThreadHistory: () => projected,
		createMessagesList: (history: any) => Object.values(history.messages),
		history: previous,
		messagesContainerElement: container,
		autoScroll,
		followScrollTop: null,
		equal,
		requestAnimationFrame: (fn: () => void) => {
			fn();
			return 1;
		},
		tick: vi.fn(async () => {
			if (state.tick.mock.calls.length === 1) container.scrollTop = 0;
		}),
		chatTitle: { set: vi.fn() },
		tags: [],
		toast: { error: vi.fn() }
	};
	for (const name of ['captureThreadAnchor', 'replaceThreadProjection', 'refreshThreadProjection'])
		state[name] = callback(name, state);
	state.refreshThreadProjection('thread');
	await vi.advanceTimersByTimeAsync(1000);
	expect(state.toast.error).not.toHaveBeenCalled();
	// The same answer remains 50px above the viewport even though earlier content grew 300px.
	expect(container.scrollTop).toBe(autoScroll ? 2000 : 550);
	expect(state.autoScroll).toBe(autoScroll);
	expect(state.followScrollTop).toBe(container.scrollTop);
	const history = state.history;
	state.buildThreadHistory = () => structuredClone(projected);
	state.tick.mockClear();
	state.refreshThreadProjection('thread');
	await vi.advanceTimersByTimeAsync(1000);
	expect(state.history).toBe(history);
	expect(state.tick).not.toHaveBeenCalled();
	expect(state.toast.error).not.toHaveBeenCalled();
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

const projection = (positions: number[], cursor: string | null): ProjectionPage => ({
	thread_id: 'thread',
	generation: 'generation',
	title: 'Grove',
	tags: [],
	preferences: {},
	messages: positions.map((position) => ({
		position,
		content: { role: position === 1 || position === 25 ? 'user' : 'assistant', text: '' },
		finished: true,
		version: `v${position}`
	})),
	next_cursor: cursor
});

it('uses whole turns when loading older messages and hides the button at the first turn', async () => {
	const state: Record<string, any> = {
		$chatId: 'thread',
		threadPage: projection([25, 26], 'before-25'),
		loadingOlderThread: false,
		localStorage: { token: 'token' },
		extendProjectionToTurn,
		mergeProjection,
		getChatById: vi
			.fn()
			.mockResolvedValueOnce(projection([21, 22, 23, 24], 'before-21'))
			.mockResolvedValueOnce(
				projection(
					Array.from({ length: 20 }, (_, i) => i + 1),
					null
				)
			),
		replaceThreadProjection: vi.fn(),
		toast: { error: vi.fn() }
	};
	state.loadThreadProjection = callback('loadThreadProjection', state);
	await callback('loadOlderThread', state)();
	expect(state.toast.error).not.toHaveBeenCalled();
	expect(state.getChatById.mock.calls).toEqual([
		['token', 'thread', 'before-25', 'thread', false],
		['token', 'thread', 'before-21', 'thread']
	]);
	expect(state.threadPage.messages).toHaveLength(26);
	expect(state.threadPage.messages[0].content.role).toBe('user');
	expect(state.threadPage.next_cursor).toBeNull();
	expect(state.loadingOlderThread).toBe(false);
	expect(state.replaceThreadProjection).toHaveBeenCalledExactlyOnceWith(state.threadPage);
	// The component gates its older-messages button on this cursor.
	expect(
		component.includes('$config?.features?.enable_v2_threads && threadPage?.next_cursor')
	).toBe(true);
});

const chatOpenState = (): Record<string, any> => ({
	$chatId: 'thread',
	chatIdProp: 'thread',
	chatId: { set: vi.fn() },
	$config: { features: { enable_v2_threads: true } },
	$temporaryChatEnabled: false,
	noteChatDebug: vi.fn(),
	localStorage: { token: 'token' },
	extendProjectionToTurn,
	getChatById: vi
		.fn()
		.mockResolvedValueOnce(projection([21, 22], 'before-21'))
		.mockResolvedValueOnce(
			projection(
				Array.from({ length: 20 }, (_, i) => i + 1),
				null
			)
		),
	threadPage: null,
	buildThreadHistory,
	history: {},
	chat: null,
	savedThreadFeatures: null,
	restoreComposerPreferences: vi.fn(),
	composerFromFeatures: vi.fn(),
	composerPreferences: vi.fn(),
	$settings: {},
	lastSavedFeatures: '',
	currentComposerPreferences: vi.fn(() => ({})),
	tags: [],
	chatTitle: { set: vi.fn() },
	selectedModels: [],
	normalizeSelectedModels: (m: any) => m
});

it('loads the initial v2 chat through whole-turn paging', async () => {
	const state = chatOpenState();
	state.loadThreadProjection = callback('loadThreadProjection', state);
	expect(await callback('loadChat', state)()).toBe('loaded');
	expect(state.getChatById).toHaveBeenCalledTimes(2);
	expect(Object.values(state.history.messages).map((m: any) => m.role)).toEqual([
		'user',
		'assistant'
	]);
	expect(state.threadPage.next_cursor).toBeNull();
});

const streamingState = () => {
	let top = 0;
	let frame = 0;
	let now = 0;
	const frames = new Map<number, () => void>();
	const container = {
		scrollHeight: 1000,
		clientHeight: 300,
		get scrollTop() {
			return top;
		},
		set scrollTop(value: number) {
			top = Math.min(value, this.scrollHeight - this.clientHeight);
		}
	};
	const state: Record<string, any> = {
		messagesContainerElement: container,
		autoScroll: true,
		$settings: {},
		history: { messages: { answer: { id: 'answer', done: false } } },
		followRAF: null,
		followMessageId: null,
		followScrollTop: null,
		isNearTop: false,
		FOLLOW_AFTER_DONE_MS: 600,
		performance: { now: () => now },
		requestAnimationFrame: (fn: () => void) => {
			frames.set(++frame, fn);
			return frame;
		},
		cancelAnimationFrame: (id: number) => frames.delete(id),
		tick: async () => {},
		scrollRAF: null
	};
	for (const name of [
		'shouldAutoScrollResponse',
		'handleResponseScroll',
		'followStreamingResponse',
		'scheduleResponseScrollToBottom'
	])
		state[name] = callback(name, state);
	const step = async (ms = 16) => {
		now += ms;
		const pending = [...frames.values()];
		frames.clear();
		for (const fn of pending) await fn();
	};
	return { state, container, step, frames };
};

it.each([undefined, true, false])('respects scrollOnResponseGeneration=%s', async (setting) => {
	const { state, container, step } = streamingState();
	state.$settings.scrollOnResponseGeneration = setting;
	state.followStreamingResponse('answer');
	await step();
	expect(container.scrollTop).toBe(setting === false ? 0 : 700);
});

it('follows height changes between chunks, stops on scroll up, and resumes at the bottom', async () => {
	const { state, container, step } = streamingState();
	state.followStreamingResponse('answer');
	await step();
	expect(container.scrollTop).toBe(700);
	// A tool/reasoning block grows after the follow loop's own scroll, before its event arrives.
	container.scrollHeight = 1500;
	state.handleResponseScroll();
	expect(state.autoScroll).toBe(true);
	await step();
	expect(container.scrollTop).toBe(1200);
	container.scrollTop = 400;
	state.handleResponseScroll();
	expect(state.autoScroll).toBe(false);
	container.scrollHeight = 1800;
	await step();
	expect(container.scrollTop).toBe(400);
	container.scrollTop = 1500;
	state.handleResponseScroll();
	expect(state.autoScroll).toBe(true);
	container.scrollHeight = 2100;
	await step();
	expect(container.scrollTop).toBe(1800);
	// Resume even if scroll height is unchanged since the last follow.
	container.scrollTop = 1796;
	state.handleResponseScroll();
	await step();
	expect(container.scrollTop).toBe(1800);
});

it('checks the follow decision again when a scheduled scroll executes', async () => {
	const { state, container, step } = streamingState();
	state.scheduleResponseScrollToBottom();
	state.autoScroll = false;
	await step();
	expect(container.scrollTop).toBe(0);
});

it('settles after done and stops when history is replaced or a new answer starts', async () => {
	const { state, container, step, frames } = streamingState();
	state.followStreamingResponse('answer');
	state.followStreamingResponse('answer');
	expect(frames.size).toBe(1);
	await step();
	state.history.messages.answer.done = true;
	await step();
	container.scrollHeight = 1300;
	await step(500);
	expect(container.scrollTop).toBe(1000);
	await step(101);
	expect(frames.size).toBe(0);
	state.history.messages.next = { id: 'next', done: false };
	state.followStreamingResponse('answer');
	state.followStreamingResponse('next');
	expect(frames.size).toBe(1);
	delete state.history.messages.next;
	await step();
	expect(frames.size).toBe(0);
});

it('starts continuous following for v2 chunks even without text', async () => {
	const { state, step, container } = streamingState();
	Object.assign(state, {
		dispatchCallOverlayAudio: vi.fn(),
		navigator: {},
		scheduleResponseScrollToBottom: vi.fn()
	});
	await callback('chatCompletionEventHandler', state)({}, state.history.messages.answer, 'thread');
	await step();
	container.scrollHeight = 1400;
	await step();
	expect(container.scrollTop).toBe(1100);
});

it('keeps already loaded older turns when refreshing the newest projection', async () => {
	vi.useFakeTimers();
	const current = projection([1, 2, 25, 26], null);
	const latest = projection([25, 26, 27], 'before-25');
	latest.messages[1].version = 'new-v26';
	const state: Record<string, any> = {
		$chatId: 'thread',
		$config: { features: { enable_v2_threads: true } },
		generationController: null,
		document: { visibilityState: 'visible' },
		threadRefreshTimer: undefined,
		threadPage: current,
		loadThreadProjection: vi.fn().mockResolvedValue(latest),
		mergeProjection,
		replaceThreadProjection: vi.fn(),
		history: { currentId: 'answer', messages: { answer: { role: 'assistant', done: true } } },
		chatTitle: { set: vi.fn() },
		tags: [],
		toast: { error: vi.fn() },
		$i18n: { t: (text: string) => text }
	};
	state.refreshThreadProjection = callback('refreshThreadProjection', state);
	state.refreshThreadProjection('thread');
	await vi.advanceTimersByTimeAsync(1000);
	expect(state.toast.error).not.toHaveBeenCalled();
	expect(state.threadPage.messages.map((m: any) => m.position)).toEqual([1, 2, 25, 26, 27]);
	expect(state.threadPage.messages.find((m: any) => m.position === 26).version).toBe('new-v26');
	expect(state.threadPage.next_cursor).toBeNull();
});

it.each([false, true])(
	'settles projection layout across frames and respects reader movement=%s',
	async (move) => {
		const container = { scrollTop: 0, scrollHeight: 1000 };
		let frame = 0;
		const projected = { currentId: null, messages: {} };
		const state: Record<string, any> = {
			$chatId: 'thread',
			generationController: null,
			messagesContainerElement: container,
			autoScroll: true,
			history: { currentId: 'old', messages: {} },
			followScrollTop: null,
			buildThreadHistory: () => projected,
			equal,
			tick: async () => {},
			requestAnimationFrame: (fn: () => void) => {
				if (++frame === 2) {
					container.scrollHeight = 1500;
					if (move) container.scrollTop = 400;
				}
				fn();
				return frame;
			}
		};
		await callback('replaceThreadProjection', state)(projection([], null));
		expect(container.scrollTop).toBe(move ? 400 : 1500);
	}
);

it.each([2, 21])(
	'forks before input position %s and resends the same input in place',
	async (position) => {
		const files = [{ id: 'file', type: 'file', name: 'Evidence.pdf' }];
		const features = { web_search: true, live_documents: 'required', document_writer: true };
		const page = projection([position, position + 1], null);
		page.messages[0].content = {
			role: 'user',
			text: 'Same question',
			files
		};
		const fork = vi.fn().mockResolvedValue({ thread_id: 'fork' });
		const state: Record<string, any> = {
			$config: { features: { enable_v2_threads: true } },
			$user: { role: 'user', permissions: { chat: { regenerate_response: true } } },
			$chatId: 'chat-alias',
			history: { currentId: 'answer' },
			readOnly: false,
			generating: false,
			generationController: null,
			regeneratingThread: false,
			taskIds: null,
			canRegenerateResponse,
			threadRegenerationInput,
			loadThreadProjection: vi.fn().mockResolvedValue(page),
			currentComposerPreferences: () => features,
			composerFeatures: (value: any) => value,
			threadAPI: () => ({ fork, versions: vi.fn().mockResolvedValue({ items: [] }) }),
			buildThreadHistory,
			replayThreadInput: vi.fn(),
			localStorage: { token: 'token' },
			goto: vi.fn(),
			refreshChatList: vi.fn().mockResolvedValue(null),
			toast: { error: vi.fn() }
		};
		state.createThreadVersion = callback('createThreadVersion', state);
		await callback(
			'regenerateResponse',
			state
		)({ id: 'answer', done: true, model: 'original-model' });
		expect(state.toast.error).not.toHaveBeenCalled();
		expect(fork).toHaveBeenCalledExactlyOnceWith('thread', position - 1, true, 'regenerate');
		expect(state.replayThreadInput).toHaveBeenCalledExactlyOnceWith({
			content: 'Same question',
			files,
			model: 'original-model',
			features,
			regenerate_from: { thread_id: 'thread', position }
		});
		expect(state.goto).not.toHaveBeenCalled();
		expect(state.$chatId).toBe('chat-alias');
		expect(state.threadPage.thread_id).toBe('fork');
		expect(state.regeneratingThread).toBe(false);
		const replay = state.replayThreadInput.mock.calls[0][0];
		Object.assign(state, {
			history: { currentId: null, messages: {} },
			restoreComposerPreferences: vi.fn(),
			composerFromFeatures: (value: any) => value,
			selectedModels: [],
			uuidv4: () => 'new-input',
			sendMessage: vi.fn()
		});
		await callback('replayThreadInput', state)(replay);
		expect(state.restoreComposerPreferences).toHaveBeenCalledExactlyOnceWith(features);
		expect(state.selectedModels).toEqual(['original-model']);
		expect(state.history.messages['new-input']).toMatchObject({
			content: 'Same question',
			files,
			models: ['original-model'],
			regenerate_from: replay.regenerate_from
		});
		expect(state.sendMessage).toHaveBeenCalledExactlyOnceWith(state.history, 'new-input', {
			modelId: 'original-model'
		});
	}
);

it.each([
	{ generating: true },
	{ generationController: {} },
	{ regeneratingThread: true },
	{ taskIds: ['task'] },
	{ readOnly: true },
	{ $user: { permissions: { chat: { regenerate_response: false } } } },
	{ history: { currentId: 'newer-answer' } }
])('does not fork when regeneration is unavailable: %j', async (override) => {
	const state = {
		$config: { features: { enable_v2_threads: true } },
		$user: {},
		readOnly: false,
		generating: false,
		generationController: null,
		regeneratingThread: false,
		taskIds: null,
		history: { currentId: 'answer' },
		canRegenerateResponse,
		loadThreadProjection: vi.fn(),
		...override
	};
	await callback('regenerateResponse', state)({ id: 'answer', done: true });
	expect(state.loadThreadProjection).not.toHaveBeenCalled();
});

it('routes reasoning chunks through the chat handler and replaces live content exactly once', async () => {
	const message = { id: 'live-answer', role: 'assistant', content: '', done: false };
	const state: Record<string, any> = {
		$config: { features: { enable_v2_threads: true } },
		$settings: {},
		history: { currentId: message.id, messages: { [message.id]: message } },
		followStreamingResponse: vi.fn(),
		dispatchCallOverlayAudio: vi.fn(),
		navigator: {},
		tick: vi.fn(),
		scheduleResponseScrollToBottom: vi.fn(),
		buildThreadHistory,
		equal,
		messagesContainerElement: null,
		autoScroll: true,
		$chatId: 'thread',
		generationController: null,
		requestAnimationFrame: (fn: () => void) => fn()
	};
	const handler = callback('chatCompletionEventHandler', state);
	await handler({ choices: [{ delta: { reasoning_content: 'Live thought' } }] }, message, 'thread');
	expect(message.content).toContain('done="false"');
	await handler({ choices: [{ delta: { content: 'Answer' } }] }, message, 'thread');
	expect(message.content).toContain('done="true"');
	const page = projection([2, 3], null);
	page.messages[0].content = { role: 'user', text: 'Question' };
	page.messages[1].content = { role: 'assistant', text: 'Answer', reasoning: 'Live thought' };
	await callback('replaceThreadProjection', state)(page);
	const answer = state.history.messages[state.history.currentId];
	expect(answer.content.match(/type="reasoning"/g)).toHaveLength(1);
	expect(answer.content).toContain('Answer');
});

it('switches versions in place with one projection load and no version-list refresh', async () => {
	const page = projection([1, 2], null);
	page.thread_id = 'older';
	const state: Record<string, any> = {
		$chatId: 'root',
		readOnly: false,
		generating: false,
		generationController: null,
		regeneratingThread: false,
		switchingThread: false,
		taskIds: null,
		threadPage: { ...page, thread_id: 'current' },
		threadVersions: [{ thread_id: 'older' }, { thread_id: 'current' }],
		loadThreadProjection: vi.fn().mockResolvedValue(page),
		buildThreadHistory,
		history: {},
		threadRefreshTimer: undefined,
		toast: { error: vi.fn() }
	};
	await callback('switchThreadVersion', state)('older');
	expect(state.loadThreadProjection).toHaveBeenCalledExactlyOnceWith('root', '', 'older');
	expect(state.threadPage.thread_id).toBe('older');
	expect(state.$chatId).toBe('root');
	expect(state.switchingThread).toBe(false);
});

it.each(['readOnly', 'generating', 'regeneratingThread', 'switchingThread'])(
	'refuses edit/switch while %s',
	async (blocked) => {
		const state: Record<string, any> = {
			readOnly: false,
			generating: false,
			generationController: null,
			regeneratingThread: false,
			switchingThread: false,
			taskIds: null,
			loadThreadProjection: vi.fn(),
			createThreadVersion: vi.fn(),
			[blocked]: true
		};
		await callback('switchThreadVersion', state)('older');
		await callback('editThreadMessage', state)('user', 'Edited');
		expect(state.loadThreadProjection).not.toHaveBeenCalled();
		expect(state.createThreadVersion).not.toHaveBeenCalled();
	}
);

it('edits a user input by forking before it and replaying edited text with original settings', async () => {
	const page = projection([2, 3, 4, 5], null);
	page.messages[0].content = {
		role: 'user',
		text: 'Before',
		files: [{ id: 'file' }],
		features: { web_search: true }
	};
	page.messages[1].content = { role: 'assistant', text: 'Answer', model: 'original-model' };
	const state: Record<string, any> = {
		$chatId: 'root',
		readOnly: false,
		generating: false,
		generationController: null,
		regeneratingThread: false,
		switchingThread: false,
		taskIds: null,
		threadPage: page,
		history: { messages: { user: { role: 'user', position: 2 } } },
		selectedModels: ['other-model'],
		threadEditInput,
		currentComposerPreferences: () => ({}),
		composerFeatures: (value: any) => value,
		threadAPI: () => ({
			fork: vi.fn().mockResolvedValue({ thread_id: 'edited' }),
			versions: vi.fn().mockResolvedValue({ items: [] })
		}),
		localStorage: { token: 'token' },
		buildThreadHistory,
		replayThreadInput: vi.fn(),
		refreshChatList: vi.fn().mockResolvedValue(null),
		toast: { error: vi.fn() }
	};
	const fork = vi.fn().mockResolvedValue({ thread_id: 'edited' });
	state.threadAPI = () => ({ fork, versions: vi.fn().mockResolvedValue({ items: [] }) });
	state.createThreadVersion = callback('createThreadVersion', state);
	await callback('editThreadMessage', state)('user', 'After');
	expect(state.toast.error).not.toHaveBeenCalled();
	expect(fork).toHaveBeenCalledExactlyOnceWith('thread', 1, true, 'edit');
	expect(state.replayThreadInput).toHaveBeenCalledExactlyOnceWith({
		content: 'After',
		files: [{ id: 'file' }],
		model: 'original-model',
		features: { web_search: true },
		edit_from: { thread_id: 'thread', position: 2 }
	});
	expect(state.$chatId).toBe('root');
	expect(state.threadPage.thread_id).toBe('edited');
});

it('opens with one current projection request and paints while the version list is pending', async () => {
	const state = chatOpenState();
	const page = { ...projection([1, 2], null), thread_id: 'latest' };
	let resolveVersions!: (value: { items: unknown[] }) => void;
	const versions = vi.fn(
		() =>
			new Promise<{ items: unknown[] }>((resolve) => {
				resolveVersions = resolve;
			})
	);
	Object.assign(state, {
		getChatById: vi.fn().mockResolvedValue(page),
		threadAPI: () => ({ versions }),
		generationController: null,
		generating: false,
		regeneratingThread: false,
		replaceThreadProjection: vi.fn(),
		toast: { error: vi.fn() }
	});
	state.loadThreadProjection = callback('loadThreadProjection', state);
	state.loadThreadVersions = callback('loadThreadVersions', state);
	expect(await callback('loadChat', state)()).toBe('loaded');
	expect(state.getChatById).toHaveBeenCalledExactlyOnceWith('token', 'thread', '', '', true);
	expect(state.threadPage.thread_id).toBe('latest');
	expect(Object.values(state.history.messages)).toHaveLength(2);
	expect(versions).toHaveBeenCalledExactlyOnceWith('latest');
	expect(state.replaceThreadProjection).not.toHaveBeenCalled();
	const items = [
		{ thread_id: 'latest', parent_thread: null, parent_position: null, version_kind: null }
	];
	resolveVersions({ items });
	await vi.waitFor(() =>
		expect(state.replaceThreadProjection).toHaveBeenCalledExactlyOnceWith(page)
	);
	expect(state.threadVersions).toEqual(items);
});

it('pins older transport pages to the resolved version instead of resolving current again', async () => {
	const state = chatOpenState();
	const latest = { ...projection([21, 22], 'older'), thread_id: 'latest' };
	const older = { ...projection([1, 2], null), thread_id: 'latest' };
	state.getChatById = vi.fn().mockResolvedValueOnce(latest).mockResolvedValueOnce(older);
	await callback('loadThreadProjection', state)('thread', '', '', true);
	expect(state.getChatById.mock.calls).toEqual([
		['token', 'thread', '', '', true],
		['token', 'thread', 'older', 'latest']
	]);
});

it('does not fetch versions for reader projections', async () => {
	const state = chatOpenState();
	state.getChatById = vi.fn().mockResolvedValue({ ...projection([1, 2], null), preferences: null });
	state.loadThreadProjection = callback('loadThreadProjection', state);
	state.loadThreadVersions = vi.fn();
	expect(await callback('loadChat', state)()).toBe('loaded');
	expect(state.chat.readonly).toBe(true);
	expect(state.loadThreadVersions).not.toHaveBeenCalled();
});

it('ignores a delayed version list after navigation', async () => {
	let resolveVersions!: (value: { items: unknown[] }) => void;
	const state: Record<string, any> = {
		$chatId: 'thread',
		threadVersionLoad: 0,
		threadPage: projection([1, 2], null),
		threadVersions: [],
		localStorage: { token: 'token' },
		threadAPI: () => ({
			versions: () =>
				new Promise((resolve) => {
					resolveVersions = resolve;
				})
		}),
		replaceThreadProjection: vi.fn(),
		toast: { error: vi.fn() }
	};
	const pending = callback('loadThreadVersions', state)('thread', state.threadPage);
	state.$chatId = 'another';
	resolveVersions({ items: [{ thread_id: 'stale' }] });
	await pending;
	expect(state.threadVersions).toEqual([]);
	expect(state.replaceThreadProjection).not.toHaveBeenCalled();
});
