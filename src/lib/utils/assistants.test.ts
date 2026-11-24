import { describe, expect, it } from 'vitest';
import {
	defaultLLMId,
	assistantDefaultLLMId,
	effectiveCapabilities,
	effectiveModel,
	isAssistant,
	isLLM,
	pickableLLMId,
	pickableLLMs,
	resolveAssistant,
	splitSelection,
	urlChoosesLLM
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
	it.each([true, false, undefined, null, 'true', 1])(
		'workspace requires exact true on both sides: %s',
		(value) => {
			expect(effectiveCapabilities({ workspace: value }, { workspace: true }).workspace).toBe(
				value === true
			);
			expect(effectiveCapabilities({ workspace: true }, { workspace: value }).workspace).toBe(
				value === true
			);
		}
	);
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
		expect(effectiveCapabilities(null)).toEqual({ vision: true, workspace: false }));
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

describe('resolveAssistant', () => {
	it('resolves an assistant id', () => {
		expect(resolveAssistant('helper', models)).toBe(assistant);
	});
	it('resolves an exact assistant name', () => {
		expect(resolveAssistant('Helper', models)).toBe(assistant);
	});
	it('prefers an id over another assistant with that exact name', () => {
		expect(
			resolveAssistant('helper', [{ ...assistant, id: 'other-helper', name: 'helper' }, ...models])
		).toBe(assistant);
	});
	it.each(['unknown', 'Hel', 'helper name', 'HELPER'])('refuses unmatched names: %s', (query) => {
		expect(resolveAssistant(query, models)).toBeUndefined();
	});
	it('refuses an LLM id even if an assistant shares that name', () => {
		expect(
			resolveAssistant('gpu', [...models, { ...assistant, id: 'other-helper', name: 'gpu' }])
		).toBeUndefined();
	});
	it('refuses an LLM name', () => {
		expect(resolveAssistant('GPU', models)).toBeUndefined();
	});
});

describe('models for an assistant that uses tools', () => {
	const catalog = (id: string, tools: string) => ({
		id,
		owned_by: 'soev',
		info: { meta: { soev: { capabilities: { tools } } } }
	});
	const plain = catalog('plain', 'unsupported');
	const capable = catalog('capable', 'supported');
	const unmeasured = catalog('unmeasured', 'unknown');
	const helper = (meta: object) => ({ id: 'helper', info: { base_model_id: 'plain', meta } });
	const all = [plain, capable, unmeasured];

	it.each([
		{ defaultFeatureIds: ['workspace'] },
		{ defaultFeatureIds: ['web_search'] },
		{ toolIds: ['server:mcp:files'] },
		{ knowledge: [{ id: 'kb' }] }
	])('leaves out a model without tool support for %o', (meta) => {
		expect(pickableLLMs(all, helper(meta)).map((model) => model.id)).toEqual([
			'capable',
			'unmeasured'
		]);
	});

	it.each([null, helper({}), helper({ defaultFeatureIds: ['image_generation'] })])(
		'offers every model without a tool-using assistant',
		(assistant) => {
			expect(pickableLLMs(all, assistant)).toEqual(all);
		}
	);

	it('replaces a selected model that cannot call tools with the preferred one that can', () => {
		const tooled = helper({ defaultFeatureIds: ['workspace'] });
		expect(pickableLLMId('plain', all, tooled, ['plain', 'unmeasured'])).toBe('unmeasured');
		expect(pickableLLMId('plain', all, tooled)).toBe('capable');
		expect(pickableLLMId('capable', all, tooled)).toBe('capable');
		expect(pickableLLMId('plain', all, null)).toBe('plain');
		expect(pickableLLMId('plain', [plain], tooled)).toBe('');
	});
});

// [Gradient] Workspace support comes from the LLM's own row.
describe('models for an assistant that uses workspace', () => {
	const assistant = {
		id: 'helper',
		info: {
			base_model_id: 'old',
			meta: { capabilities: { workspace: true }, defaultFeatureIds: ['workspace'] }
		}
	};
	const supported = { id: 'supported', info: { meta: { capabilities: { workspace: true } } } };
	const all = [
		{ id: 'missing' },
		{ id: 'off', info: { meta: { capabilities: { workspace: false } } } },
		{ id: 'truthy', info: { meta: { capabilities: { workspace: 'true' } } } },
		{
			id: 'no-tools',
			info: {
				meta: {
					capabilities: { workspace: true },
					soev: { capabilities: { tools: 'unsupported' } }
				}
			}
		},
		supported
	];
	it('requires workspace and tool support together', () => {
		expect(pickableLLMs(all, assistant)).toEqual([supported]);
		expect(pickableLLMId('off', all, assistant, ['off', 'supported'])).toBe('supported');
		expect(pickableLLMId('supported', all, assistant)).toBe('supported');
		expect(pickableLLMId('', all, assistant)).toBe('supported');
	});
	it('does not fall back to an unsupported LLM when none qualify', () => {
		const unavailable = all.filter((model) => model.id !== 'supported');
		expect(pickableLLMs(unavailable, assistant)).toEqual([]);
		expect(pickableLLMId('off', unavailable, assistant)).toBe('');
	});
	it('leaves plain chats unchanged', () => {
		expect(pickableLLMs(all)).toEqual(all);
		expect(effectiveModel(supported)).toBe(supported);
	});
});

// [Gradient] The default only seeds a newly activated assistant in an untouched empty chat.
describe('assistant default LLM', () => {
	const available = [
		{ id: 'plain', info: { meta: { soev: { capabilities: { tools: 'unsupported' } } } } },
		{ id: 'capable', info: { meta: { capabilities: { workspace: true } } } },
		{ id: 'no-workspace' },
		assistant
	];
	const helper = (meta: object) => ({ id: 'helper', info: { base_model_id: 'plain', meta } });
	const initial = { emptyChat: true, explicitModel: false, selectionLocked: false };
	it('uses an available default without changing inputs', () => {
		const selected = helper({ defaultModelId: 'capable' });
		const before = structuredClone([available, selected]);
		expect(assistantDefaultLLMId(available, selected, initial)).toBe('capable');
		expect([available, selected]).toEqual(before);
	});
	it.each([
		{ ...initial, emptyChat: false },
		{ ...initial, explicitModel: true },
		{ ...initial, selectionLocked: true }
	])('does not replace a chosen or saved model: %o', (context) => {
		expect(
			assistantDefaultLLMId(available, helper({ defaultModelId: 'capable' }), context)
		).toBeUndefined();
	});
	it.each([
		{},
		{ defaultModelId: '' },
		{ defaultModelId: 'deleted' },
		{ defaultModelId: 'helper' },
		{ defaultModelId: 'plain', toolIds: ['tool'] },
		{ defaultModelId: 'no-workspace', capabilities: { workspace: true } }
	])('keeps current behaviour for an absent or unpickable default: %o', (meta) => {
		expect(assistantDefaultLLMId(available, helper(meta), initial)).toBeUndefined();
	});
	it('has no default without an assistant or accessible models', () => {
		expect(assistantDefaultLLMId(available, null, initial)).toBeUndefined();
		expect(
			assistantDefaultLLMId([], helper({ defaultModelId: 'capable' }), initial)
		).toBeUndefined();
	});
});

describe('urlChoosesLLM', () => {
	it.each([
		['', false],
		['model=helper', false],
		['model=gpu', true],
		['models=helper,gpu', true],
		['model=deleted', false]
	])('%s -> %s', (query, expected) => {
		expect(urlChoosesLLM(new URLSearchParams(query), models)).toBe(expected);
	});
});
