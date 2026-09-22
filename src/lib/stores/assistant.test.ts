import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { createInstance } from 'i18next';
import { toast } from 'svelte-sonner';
import { selectAssistant } from '$lib/utils/assistantSelection';
import { get } from 'svelte/store';

vi.mock('svelte-sonner', () => ({ toast: { message: vi.fn(), error: vi.fn() } }));

vi.mock('$lib/stores', async () => {
	const { writable } = await import('svelte/store');
	return { models: writable([]), config: writable({}), settings: writable({}) };
});

import { models } from '$lib/stores';
import {
	activeAssistant,
	activeAssistantId,
	effectiveModels,
	llmSelection,
	reconcileFolderSelection,
	reconcileAssistantSelection
} from './assistant';

const llm = { id: 'gpu', name: 'GPU', owned_by: 'openai' as const, external: false };
const assistant = {
	...llm,
	id: 'helper',
	name: 'Helper',
	info: { base_model_id: 'gpu', meta: { description: 'Hello' } }
};

const i18n = createInstance();
beforeAll(async () => {
	await i18n.init({ lng: 'en', resources: { en: { translation: {} } } });
});

beforeEach(() => {
	vi.clearAllMocks();
	activeAssistantId.set(null);
	models.set([
		llm,
		assistant,
		{ ...assistant, id: 'url' },
		{ ...assistant, id: 'saved' }
	] as (typeof llm)[]);
});

afterEach(() => vi.unstubAllGlobals());

describe('assistant stores', () => {
	it('returns the same array with no active assistant', () => {
		expect(get(effectiveModels)).toBe(get(models));
		activeAssistantId.set('helper');
		expect(get(effectiveModels)).not.toBe(get(models));
		expect(get(effectiveModels)[0]).toMatchObject({
			id: 'gpu',
			name: 'GPU',
			assistant_id: 'helper'
		});
		expect(get(activeAssistant)).toBe(assistant);
		activeAssistantId.set(null);
		expect(get(effectiveModels)).toBe(get(models));
	});
	it('restores saved binding before URL and legacy selection, then clears on new chat', () => {
		expect(reconcileAssistantSelection(['helper'], 'url', 'saved')).toEqual(['gpu']);
		expect(get(activeAssistantId)).toBe('saved');
		reconcileAssistantSelection(['helper'], 'url');
		expect(get(activeAssistantId)).toBe('url');
		reconcileAssistantSelection(['helper']);
		expect(get(activeAssistantId)).toBe('helper');
		reconcileAssistantSelection(['gpu']);
		expect(get(activeAssistantId)).toBeNull();
	});
	it.each(['unknown', 'gpu'])('ignores a URL id that is not an available assistant: %s', (id) => {
		expect(reconcileAssistantSelection(['gpu'], id)).toEqual(['gpu']);
		expect(get(activeAssistantId)).toBeNull();
		expect(get(effectiveModels)).toBe(get(models));
	});
	it('falls through an invalid URL id to the legacy assistant selection', () => {
		expect(reconcileAssistantSelection(['helper'], 'unknown')).toEqual(['gpu']);
		expect(get(activeAssistantId)).toBe('helper');
	});
	it('falls through a deleted saved assistant to a valid URL assistant', () => {
		expect(reconcileAssistantSelection(['gpu'], 'url', 'deleted')).toEqual(['gpu']);
		expect(get(activeAssistantId)).toBe('url');
	});
	it('opens a chat with a deleted assistant as a plain LLM chat', () => {
		activeAssistantId.set('helper');
		expect(reconcileAssistantSelection(['gpu'], null, 'deleted')).toEqual(['gpu']);
		expect(get(activeAssistantId)).toBeNull();
		expect(llmSelection(['gpu'])).toEqual(['gpu']);
		expect(get(effectiveModels)).toBe(get(models));
	});
	it('clears an assistant removed from a loaded registry', () => {
		activeAssistantId.set('helper');
		models.set([llm]);
		expect(get(activeAssistantId)).toBeNull();
	});
	it('migrates empty folder selections without rebinding saved chats', () => {
		expect(reconcileFolderSelection(['helper'], true)).toEqual(['gpu']);
		expect(get(activeAssistantId)).toBe('helper');
		reconcileAssistantSelection(['gpu'], null, 'saved');
		expect(reconcileFolderSelection(['helper'], false)).toEqual(['gpu']);
		expect(get(activeAssistantId)).toBe('saved');
	});

	it('prevents compare and legacy regeneration from changing assistant identity', () => {
		reconcileAssistantSelection(['gpu', 'helper', 'gpu']);
		expect(llmSelection(['helper', 'gpu'])).toEqual(['gpu']);
		expect(get(activeAssistantId)).toBe('helper');
	});
});

describe('assistant command selection', () => {
	it('updates the same identity and effective metadata used by the chip and placeholder', () => {
		selectAssistant('helper', true, i18n);
		expect(get(activeAssistantId)).toBe('helper');
		expect(get(activeAssistant)).toBe(assistant);
		expect(get(effectiveModels)[0]).toMatchObject({
			id: 'gpu',
			info: { meta: { description: 'Hello' } }
		});
		expect(get(models)[0]).toBe(llm);
	});
	it('refuses to switch assistants once the chat has a message', () => {
		activeAssistantId.set('saved');
		selectAssistant('helper', false, i18n);
		expect(get(activeAssistantId)).toBe('saved');
		expect(toast.error).toHaveBeenCalledWith('Start a new chat to switch assistants.');
	});
	it.each(['unknown', 'gpu'])(
		'keeps the selection when the requested assistant is unavailable: %s',
		(query) => {
			activeAssistantId.set('helper');
			selectAssistant(query, true, i18n);
			expect(get(activeAssistantId)).toBe('helper');
			expect(toast.error).toHaveBeenCalledWith(`Assistant not found: ${query}`);
		}
	);
	it('reports no selection or the current name without changing the selection', () => {
		selectAssistant('', true, i18n);
		expect(toast.message).toHaveBeenLastCalledWith('No assistant selected');
		activeAssistantId.set('helper');
		selectAssistant('', false, i18n);
		expect(toast.message).toHaveBeenLastCalledWith('Current assistant: Helper');
		expect(get(activeAssistantId)).toBe('helper');
	});
});

const restoreSession = async (id: string, loaded = false) => {
	vi.resetModules();
	const values = new Map([['activeAssistantId', id]]);
	const sessionStorage = {
		getItem: (key: string) => values.get(key) ?? null,
		setItem: (key: string, value: string) => {
			values.set(key, value);
		},
		removeItem: (key: string) => {
			values.delete(key);
		}
	};
	vi.stubGlobal('window', { sessionStorage });
	const { models: registry } = await import('$lib/stores');
	registry.set(loaded ? ([llm, assistant] as (typeof llm)[]) : []);
	const stores = await import('./assistant');
	return { registry, sessionStorage, ...stores };
};

describe('session restoration', () => {
	it('retains a restored id while models are empty, then validates the loaded registry', async () => {
		const restored = await restoreSession('helper');
		expect(get(restored.activeAssistantId)).toBe('helper');
		expect(restored.sessionStorage.getItem('activeAssistantId')).toBe('helper');
		restored.registry.set([llm, assistant] as (typeof llm)[]);
		expect(get(restored.activeAssistantId)).toBe('helper');
		expect(get(restored.activeAssistant)).toBe(assistant);
		restored.registry.set([]);
		expect(get(restored.activeAssistantId)).toBe('helper');
	});
	it.each(['deleted', 'gpu'])(
		'clears an invalid restored id when models arrive: %s',
		async (id) => {
			const restored = await restoreSession(id);
			expect(get(restored.activeAssistantId)).toBe(id);
			restored.registry.set([llm, assistant] as (typeof llm)[]);
			expect(get(restored.activeAssistantId)).toBeNull();
			expect(restored.sessionStorage.getItem('activeAssistantId')).toBeNull();
		}
	);
	it('validates storage immediately if the registry was already loaded', async () => {
		const restored = await restoreSession('deleted', true);
		expect(get(restored.activeAssistantId)).toBeNull();
		expect(restored.sessionStorage.getItem('activeAssistantId')).toBeNull();
	});
});
