import { beforeEach, describe, expect, it, vi } from 'vitest';
import { get, writable } from 'svelte/store';

vi.mock('$lib/stores', () => ({
	models: writable([]),
	config: writable({}),
	settings: writable({})
}));

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

beforeEach(() => {
	activeAssistantId.set(null);
	models.set([llm, assistant] as (typeof llm)[]);
});

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
