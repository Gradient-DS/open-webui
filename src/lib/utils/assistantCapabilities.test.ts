import { describe, it, expect } from 'vitest';
import {
	togglesFromMeta,
	applyToggles,
	type AssistantToggles
} from './assistantCapabilities';

const ALL_ON: AssistantToggles = {
	web_search: true,
	image_generation: true,
	code_interpreter: true,
	vision: true,
	file_upload: true,
	citations: true
};
const ALL_OFF: AssistantToggles = {
	web_search: false,
	image_generation: false,
	code_interpreter: false,
	vision: false,
	file_upload: false,
	citations: false
};

describe('applyToggles', () => {
	it('expands web_search to capabilities, defaultFeatureIds and builtinTools', () => {
		const meta = applyToggles({}, { ...ALL_OFF, web_search: true });
		expect(meta.capabilities.web_search).toBe(true);
		expect(meta.defaultFeatureIds).toContain('web_search');
		expect(meta.builtinTools.web_search).toBe(true);
	});

	it('maps file_upload to both file_upload and file_context capabilities', () => {
		const meta = applyToggles({}, { ...ALL_OFF, file_upload: true });
		expect(meta.capabilities.file_upload).toBe(true);
		expect(meta.capabilities.file_context).toBe(true);
	});

	it('preserves advanced meta fields it does not own', () => {
		const meta = applyToggles(
			{ toolIds: ['t1'], capabilities: { document_writer: true } },
			ALL_OFF
		);
		expect(meta.toolIds).toEqual(['t1']);
		expect(meta.capabilities.document_writer).toBe(true);
	});

	it('removes a feature id when its toggle is turned off', () => {
		const meta = applyToggles(
			{ defaultFeatureIds: ['web_search', 'image_generation'] },
			{ ...ALL_OFF, image_generation: true }
		);
		expect(meta.defaultFeatureIds).not.toContain('web_search');
		expect(meta.defaultFeatureIds).toContain('image_generation');
	});
});

describe('round-trip togglesFromMeta(applyToggles(...))', () => {
	for (const sample of [ALL_ON, ALL_OFF, { ...ALL_OFF, web_search: true, citations: true }]) {
		it(`is identity for ${JSON.stringify(sample)}`, () => {
			expect(togglesFromMeta(applyToggles({}, sample))).toEqual(sample);
		});
	}
});

describe('togglesFromMeta', () => {
	it('reads all six toggles from capabilities, defaulting missing to false', () => {
		expect(togglesFromMeta({})).toEqual(ALL_OFF);
		expect(togglesFromMeta({ capabilities: { vision: true } })).toEqual({
			...ALL_OFF,
			vision: true
		});
	});
});
