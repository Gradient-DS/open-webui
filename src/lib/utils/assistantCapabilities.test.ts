import { describe, it, expect } from 'vitest';
import {
	togglesFromMeta,
	applyToggles,
	capabilityOffered,
	simpleCapabilityOffered,
	type AssistantToggles
} from './assistantCapabilities';

const ALL_ON: AssistantToggles = {
	web_search: true,
	image_generation: true,
	code_interpreter: true,
	document_writer: true,
	live_documents: true,
	live_mail: true,
	vision: true,
	file_upload: true,
	citations: true
};
const ALL_OFF: AssistantToggles = {
	web_search: false,
	image_generation: false,
	code_interpreter: false,
	document_writer: false,
	live_documents: false,
	live_mail: false,
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

	it('maps document_writer to a capability and a default feature, but NOT a builtin tool', () => {
		const meta = applyToggles({}, { ...ALL_OFF, document_writer: true });
		expect(meta.capabilities.document_writer).toBe(true);
		expect(meta.defaultFeatureIds).toContain('document_writer');
		expect(meta.builtinTools.document_writer).toBeUndefined();
	});

	it('maps live_documents and live_mail to capabilities and default features, not builtin tools', () => {
		const meta = applyToggles({}, { ...ALL_OFF, live_documents: true, live_mail: true });
		expect(meta.capabilities.live_documents).toBe(true);
		expect(meta.capabilities.live_mail).toBe(true);
		expect(meta.defaultFeatureIds).toEqual(['live_documents', 'live_mail']);
		expect(meta.builtinTools.live_documents).toBeUndefined();
		expect(meta.builtinTools.live_mail).toBeUndefined();
	});

	it('maps file_upload to both file_upload and file_context capabilities', () => {
		const meta = applyToggles({}, { ...ALL_OFF, file_upload: true });
		expect(meta.capabilities.file_upload).toBe(true);
		expect(meta.capabilities.file_context).toBe(true);
	});

	it('preserves advanced meta fields it does not own', () => {
		const meta = applyToggles({ toolIds: ['t1'], capabilities: { status_updates: true } }, ALL_OFF);
		expect(meta.toolIds).toEqual(['t1']);
		expect(meta.capabilities.status_updates).toBe(true);
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
	for (const sample of [
		ALL_ON,
		ALL_OFF,
		{ ...ALL_OFF, web_search: true, citations: true },
		{ ...ALL_OFF, document_writer: true, vision: true }
	]) {
		it(`is identity for ${JSON.stringify(sample)}`, () => {
			expect(togglesFromMeta(applyToggles({}, sample))).toEqual(sample);
		});
	}
});

describe('togglesFromMeta', () => {
	it('reads all toggles from capabilities, defaulting missing to false', () => {
		expect(togglesFromMeta({})).toEqual(ALL_OFF);
		expect(togglesFromMeta({ capabilities: { vision: true } })).toEqual({
			...ALL_OFF,
			vision: true
		});
	});
});

describe('capabilityOffered', () => {
	it('needs every tenant flag of a gated capability', () => {
		expect(capabilityOffered('live_documents', { enable_live_documents: true })).toBe(true);
		expect(capabilityOffered('live_mail', { enable_live_documents: true })).toBe(false);
		expect(capabilityOffered('web_search', undefined)).toBe(false);
	});

	it('needs both document writer flags', () => {
		expect(capabilityOffered('document_writer', { enable_document_writer: true })).toBe(false);
		expect(capabilityOffered('document_writer', { feature_document_writer: true })).toBe(false);
		expect(
			capabilityOffered('document_writer', {
				enable_document_writer: true,
				feature_document_writer: true
			})
		).toBe(true);
	});

	it('offers ungated capabilities always', () => {
		expect(capabilityOffered('vision', {})).toBe(true);
	});
});

describe('simpleCapabilityOffered', () => {
	const features = {
		enable_image_generation: true,
		enable_code_interpreter: true,
		enable_web_search: true
	};

	it('hides image generation and code interpreter on agent-routed tenants', () => {
		const agent = { ...features, feature_agent_api_enabled: true };
		expect(simpleCapabilityOffered('image_generation', agent)).toBe(false);
		expect(simpleCapabilityOffered('code_interpreter', agent)).toBe(false);
		expect(simpleCapabilityOffered('web_search', agent)).toBe(true);
	});

	it('keeps them on tenants without the agent', () => {
		expect(simpleCapabilityOffered('image_generation', features)).toBe(true);
		expect(simpleCapabilityOffered('code_interpreter', features)).toBe(true);
	});
});
