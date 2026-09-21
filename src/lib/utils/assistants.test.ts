import { describe, expect, it } from 'vitest';
import {
	defaultLLMId,
	effectiveCapabilities,
	effectiveModel,
	isAssistant,
	isLLM,
	splitSelection
} from './assistants';

const llm = {
	id: 'gpu',
	name: 'GPU',
	owned_by: 'openai',
	external: true,
	urlIdx: 2,
	info: {
		base_model_id: null,
		meta: { description: 'Base', tags: ['base'], capabilities: { vision: false, citations: true } },
		params: { temperature: 0.2, seed: 1 }
	}
};
const assistant = {
	id: 'helper',
	name: 'Helper',
	preset: true,
	info: {
		base_model_id: 'gpu',
		meta: {
			description: 'Assistant',
			knowledge: ['doc'],
			capabilities: { vision: true, citations: false }
		},
		params: { temperature: 0.8 }
	}
};
const other = { id: 'other', name: 'Other' };
const models = [llm, assistant, other];

describe('effectiveCapabilities', () => {
	it.each([
		[true, true, true],
		[true, false, false],
		[false, true, false],
		[false, false, false]
	])('vision %s AND %s = %s', (base, override, expected) => {
		expect(effectiveCapabilities({ vision: base }, { vision: override }).vision).toBe(expected);
	});
	it.each([
		[undefined, undefined, true],
		[undefined, false, false],
		[false, undefined, false],
		[true, undefined, true],
		[undefined, true, true],
		[null, true, false]
	])('defaults only unset vision: %s, %s', (base, override, expected) => {
		expect(effectiveCapabilities({ vision: base }, { vision: override }).vision).toBe(expected);
	});
	it.each([
		[{}, {}, undefined],
		[{ web_search: true }, {}, true],
		[{}, { web_search: false }, false],
		[{ web_search: false }, { web_search: true }, true],
		[{ web_search: true }, { web_search: false }, false]
	])('uses explicit assistant choices for other keys', (base, override, expected) => {
		expect(effectiveCapabilities(base, override).web_search).toBe(expected);
	});
	it('accepts absent capability maps', () =>
		expect(effectiveCapabilities(null)).toEqual({ vision: true }));
});

describe('effectiveModel', () => {
	it('preserves LLM identity and connections while overlaying info', () => {
		const before = structuredClone(models);
		const result = effectiveModel(llm, assistant);
		expect(result).toMatchObject({
			id: 'gpu',
			name: 'GPU',
			owned_by: 'openai',
			external: true,
			urlIdx: 2,
			assistant_id: 'helper',
			info: {
				meta: {
					description: 'Assistant',
					tags: ['base'],
					knowledge: ['doc'],
					capabilities: { vision: false, citations: false }
				},
				params: { temperature: 0.8, seed: 1 }
			}
		});
		expect(result.info).not.toHaveProperty('base_model_id');
		expect(isLLM(result)).toBe(true);
		expect(models).toEqual(before);
	});
	it('preserves reference without an assistant', () => {
		expect(effectiveModel(llm)).toBe(llm);
		expect(effectiveModel(llm, null)).toBe(llm);
	});
});

describe('classification', () => {
	it.each([
		[other, true],
		[llm, true],
		[assistant, false],
		[{ id: 'arena', arena: true }, false],
		[{ id: 'arena', owned_by: 'arena' }, false],
		[{ id: 'preset', preset: true }, false],
		[null, false]
	])('identifies LLMs: %j', (model, expected) => expect(isLLM(model)).toBe(expected));
	it('recognizes a non-null legacy marker even when empty', () => {
		expect(isAssistant({ id: 'legacy', info: { base_model_id: '' } })).toBe(true);
		expect(isAssistant(llm)).toBe(false);
	});
});

describe('splitSelection', () => {
	it.each([
		[['helper'], 'other', ['other'], 'helper'],
		[['helper'], '', ['gpu'], 'helper'],
		[['helper'], 'missing', ['gpu'], 'helper'],
		[['gpu', 'helper', 'other'], '', ['gpu', 'other'], 'helper'],
		[['helper', 'other'], 'gpu', ['other'], 'helper'],
		[['unknown'], '', ['unknown'], null],
		[['unknown', 'helper'], '', ['unknown', 'gpu'], 'helper'],
		[['gpu', 'gpu', 'helper', 'helper'], '', ['gpu'], 'helper']
	] as [string[], string, string[], string | null][])(
		'normalizes %j with fallback %s',
		(ids, fallback, llmIds, assistantId) => {
			expect(splitSelection(ids, models, fallback)).toEqual({ llmIds, assistantId });
		}
	);
	it('leaves an empty slot when the base model is gone', () => {
		expect(splitSelection(['helper'], [assistant], '')).toEqual({
			llmIds: [''],
			assistantId: 'helper'
		});
	});
	it('uses the first assistant and deduplicates replacement slots', () => {
		expect(
			splitSelection(
				['helper', 'second', 'unknown', 'unknown'],
				[...models, { ...assistant, id: 'second' }],
				'other'
			)
		).toEqual({ llmIds: ['other', 'unknown'], assistantId: 'helper' });
	});
	it('excludes known arena and preset entries', () => {
		expect(splitSelection(['arena', 'gpu'], [...models, { id: 'arena', arena: true }], '')).toEqual(
			{ llmIds: ['gpu'], assistantId: null }
		);
	});
	it('chooses only LLM defaults', () =>
		expect(defaultLLMId(models, ['helper', 'other'])).toBe('other'));
});
