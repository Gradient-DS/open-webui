import { readFileSync } from 'node:fs';
import { parse } from 'svelte/compiler';
import ts from 'typescript';
import { expect, it, vi } from 'vitest';
import { buildThreadHistory } from '$lib/utils/threads';
import type { ProjectionPage } from '$lib/apis/threads';

// Exercise the preview lifecycle without mounting the full message renderer.
const component = readFileSync('src/lib/components/layout/Sidebar/ChatHoverPreview.svelte', 'utf8');
const script = ts.createSourceFile(
	'ChatHoverPreview.ts',
	component.slice(component.indexOf('>') + 1, component.indexOf('</script>')),
	ts.ScriptTarget.Latest
);
const evaluate = (source: string, state: Record<string, any>) => {
	const js = ts.transpileModule(`const value = (${source});`, {
		compilerOptions: { target: ts.ScriptTarget.ESNext, module: ts.ModuleKind.None }
	}).outputText;
	return new Function('state', `with (state) { ${js}; return value; }`)(state);
};
const callback = (name: string, state: Record<string, any>) => {
	let expression: ts.Node | undefined;
	const visit = (node: ts.Node) => {
		if (ts.isVariableDeclaration(node) && node.name.getText(script) === name)
			expression = node.initializer;
		ts.forEachChild(node, visit);
	};
	visit(script);
	if (!expression) throw new Error(`Missing callback: ${name}`);
	return evaluate(expression.getText(script), state);
};
const nodes = (root: any, predicate: (node: any) => boolean): any[] => {
	if (!root || typeof root !== 'object') return [];
	return [
		...(predicate(root) ? [root] : []),
		...Object.values(root).flatMap((value) =>
			Array.isArray(value)
				? value.flatMap((item) => nodes(item, predicate))
				: nodes(value, predicate)
		)
	];
};
const page = (): ProjectionPage => ({
	thread_id: 'current-version',
	generation: 'generation',
	title: 'Conversation',
	tags: [],
	preferences: {},
	messages: [
		{ position: 1, content: { role: 'user', text: 'Question' }, finished: true, version: 'v1' },
		{ position: 2, content: { role: 'assistant', text: 'Answer' }, finished: true, version: 'v2' }
	],
	next_cursor: null
});
const previewState = () => ({
	chatId: 'conversation',
	openPreview: false,
	$config: { features: { enable_v2_threads: true } },
	$i18n: { t: (key: string) => key },
	localStorage: { token: 'token' },
	loadToken: 0,
	requestedChatId: '',
	loading: false,
	error: '',
	previewReady: false,
	history: null as ReturnType<typeof buildThreadHistory> | null,
	selectedModels: [''],
	getChatById: vi.fn().mockResolvedValue(page()),
	buildThreadHistory,
	scrollPreviewToBottom: vi.fn()
});

it('loads one current projection only when the preview opens and renders its messages', async () => {
	const state = previewState();
	const load = callback('loadChatPreview', state);
	const [loadTrigger] = nodes(
		script,
		(node) =>
			ts.isLabeledStatement(node) && node.getText(script).includes('loadChatPreview(chatId)')
	);
	const trigger = async () => {
		if (evaluate(loadTrigger.statement.expression.getText(script), state)) await load(state.chatId);
	};
	await trigger();
	expect(state.getChatById).not.toHaveBeenCalled();
	state.openPreview = true;
	await trigger();
	await trigger();
	expect(state.getChatById).toHaveBeenCalledExactlyOnceWith('token', 'conversation', '', '', true);
	expect(Object.values(state.history!.messages).map((message) => message.content)).toEqual([
		'Question',
		'Answer'
	]);
	expect(state.history!.currentId).toContain('current-version:');
	expect(state.previewReady).toBe(true);
});

it('reloads the current version when the preview is reopened', async () => {
	const state = previewState();
	const load = callback('loadChatPreview', state);
	await load(state.chatId);
	callback('resetChatPreview', state)();
	const next = page();
	next.thread_id = 'regenerated-version';
	state.getChatById.mockResolvedValue(next);
	await load(state.chatId);
	expect(state.getChatById).toHaveBeenCalledTimes(2);
	expect(state.history!.currentId).toContain('regenerated-version:');
});

it('ignores a late projection after the preview closes', async () => {
	const state = previewState();
	let resolve!: (value: ProjectionPage) => void;
	state.getChatById.mockReturnValue(new Promise((done) => (resolve = done)));
	const loading = callback('loadChatPreview', state)(state.chatId);
	callback('resetChatPreview', state)();
	resolve(page());
	await loading;
	expect(state.history).toBeNull();
	expect(state.previewReady).toBe(false);
	expect(state.loading).toBe(false);
	expect(state.scrollPreviewToBottom).not.toHaveBeenCalled();
});

it('lets a changed conversation replace an in-flight preview', async () => {
	const state = previewState();
	let resolve!: (value: ProjectionPage) => void;
	state.getChatById.mockReturnValueOnce(new Promise((done) => (resolve = done)));
	const load = callback('loadChatPreview', state);
	const previous = load(state.chatId);
	const next = page();
	next.thread_id = 'other-version';
	state.getChatById.mockResolvedValue(next);
	await load('other-conversation');
	resolve(page());
	await previous;
	expect(state.history!.currentId).toContain('other-version:');
	expect(state.getChatById).toHaveBeenCalledTimes(2);
});

it.each([true, false])(
	'hides an empty preview, v2=%s, including the welcome renderer',
	async (v2) => {
		const state = previewState();
		state.$config.features.enable_v2_threads = v2;
		state.openPreview = true;
		state.getChatById.mockResolvedValue(
			v2 ? { ...page(), messages: [] } : { chat: { history: { messages: {}, currentId: null } } }
		);
		await callback('loadChatPreview', state)(state.chatId);
		const guards = nodes(parse(component).html, (node) => node.type === 'IfBlock').filter((node) =>
			component.slice(node.expression.start, node.expression.end).includes('history?.currentId')
		);
		expect(guards).toHaveLength(2);
		for (const guard of guards)
			expect(
				Boolean(evaluate(component.slice(guard.expression.start, guard.expression.end), state))
			).toBe(false);
	}
);

it('keeps legacy preview messages readable', async () => {
	const state = previewState();
	state.$config.features.enable_v2_threads = false;
	const history = buildThreadHistory(page(), []);
	state.getChatById.mockResolvedValue({ chat: { history, models: ['legacy-model'] } });
	await callback('loadChatPreview', state)(state.chatId);
	expect(state.history).toBe(history);
	expect(state.selectedModels).toEqual(['legacy-model']);
	expect(state.previewReady).toBe(true);
});

it('keeps destructive actions in the menu and preserves the delete confirmation', () => {
	const source = readFileSync('src/lib/components/layout/Sidebar/ChatItem.svelte', 'utf8');
	const components = nodes(parse(source).html, (node) => node.type === 'InlineComponent');
	expect(components.map((node) => node.name)).not.toContain('ArchiveBoxIcon');
	expect(components.map((node) => node.name)).not.toContain('GarbageBinIcon');
	const [menu] = components.filter((node) => node.name === 'ChatMenu');
	expect(menu.attributes.map((attribute: any) => attribute.name)).toEqual(
		expect.arrayContaining([
			'renameHandler',
			'archiveChatHandler',
			'deleteHandler',
			'markUnreadHandler'
		])
	);
	const [confirm] = components.filter((node) => node.name === 'DeleteConfirmDialog');
	expect(confirm.attributes.find((attribute: any) => attribute.name === 'show')).toMatchObject({
		type: 'Binding',
		expression: { name: 'showDeleteConfirm' }
	});
	expect(source).not.toContain('shiftKey && mouseOver');
});
