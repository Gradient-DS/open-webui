import { describe, expect, it } from 'vitest';

import {
	CURRENT_RELEASE,
	RELEASES,
	newModels,
	shouldShowWhatsNew,
	whatsNewSlides,
	type Release
} from './whatsNew';

const user = (features: Record<string, boolean> = {}) => ({
	role: 'user',
	permissions: { features }
});

const release: Release = {
	version: '9.9',
	notesUrl: 'https://docs.example/notes',
	slides: ['auto_mode', 'citations', 'models', 'outlook', 'onedrive'],
	models: [{ match: /model-a/, description: 'A for everything' }]
};

const slides = (
	features: Record<string, unknown>,
	u: ReturnType<typeof user> | { role: string },
	models: { name: string }[] = []
) => whatsNewSlides(release, { features, user: u, models });

describe('whatsNewSlides', () => {
	it('shows only citations when the tenant has none of the gated features', () => {
		expect(slides({}, user())).toEqual(['citations']);
	});

	it('shows every slide for a tenant with all features', () => {
		const features = {
			enable_web_search: true,
			enable_document_writer: true,
			enable_live_mail: true,
			enable_live_documents: true
		};
		expect(slides(features, user({ web_search: true }), [{ name: 'Model A' }])).toEqual([
			'auto_mode',
			'citations',
			'models',
			'outlook',
			'onedrive'
		]);
	});

	it('skips auto mode when the user may not use either tool', () => {
		expect(slides({ enable_web_search: true, enable_document_writer: true }, user())).toEqual([
			'citations'
		]);
	});

	it('lets admins bypass tool permissions', () => {
		expect(slides({ enable_web_search: true }, { role: 'admin' })).toContain('auto_mode');
	});

	it('skips auto mode when the document writer tier flag is off', () => {
		const features = { enable_document_writer: true, feature_document_writer: false };
		expect(slides(features, user({ document_writer: true }))).toEqual(['citations']);
	});

	it('shows Outlook without OneDrive', () => {
		expect(slides({ enable_live_mail: true }, user())).toEqual(['citations', 'outlook']);
	});

	it('keeps the order and choice of the release', () => {
		const only = { ...release, slides: ['onedrive', 'citations'] as Release['slides'] };
		const features = { enable_live_documents: true, enable_live_mail: true };
		expect(whatsNewSlides(only, { features, user: user(), models: [] })).toEqual([
			'onedrive',
			'citations'
		]);
	});
});

describe('newModels', () => {
	it('tells GLM-5.3 and its Flash variant apart, by their ids', () => {
		const names = newModels(RELEASES[0], [
			{ id: 'zai-org/glm-5.3' },
			{ id: 'zai-org/glm-5.3-flash' }
		]).map((model) => model.name);
		expect(names).toEqual(['zai-org/glm-5.3', 'zai-org/glm-5.3-flash']);
	});

	it('ignores hidden models, assistants and older models', () => {
		const models = [
			{ id: 'mistral-large-4', info: { meta: { hidden: true } } },
			{ id: 'my-assistant-glm-5.3', info: { base_model_id: 'zai-org/glm-5.3' } },
			{ id: 'zai-org/glm-5.2-fp8' },
			{ id: 'google/gemma-4-31b-it' }
		];
		expect(newModels(RELEASES[0], models)).toEqual([]);
	});

	it("uses the tenant's list for this release, in its order", () => {
		const models = [
			{ id: 'model-a', name: 'Model A (EU)' },
			{ id: 'vendor/model-b', name: 'Model B' },
			{ id: 'vendor/model-c', name: 'Model C' }
		];
		const configured = { '9.9': ['vendor/model-b', 'model-a', 'vendor/not-in-picker'] };
		expect(newModels(release, models, configured)).toEqual([
			{ name: 'vendor/model-b', description: undefined },
			{ name: 'model-a', description: 'A for everything' }
		]);
	});

	it('announces none when the tenant lists none', () => {
		expect(newModels(release, [{ id: 'model-a' }], { '9.9': [] })).toEqual([]);
	});

	it("ignores a tenant's list for another release", () => {
		expect(newModels(release, [{ id: 'model-a' }], { '9.8': [] })).toEqual([
			{ name: 'model-a', description: 'A for everything' }
		]);
	});
});

describe('shouldShowWhatsNew', () => {
	it('shows once per minor release', () => {
		expect(shouldShowWhatsNew(undefined, user(), release)).toBe(true);
		expect(shouldShowWhatsNew('9.8', user(), release)).toBe(true);
		expect(shouldShowWhatsNew('9.9', user(), release)).toBe(false);
	});

	it('announces the last release in the registry', () => {
		expect(CURRENT_RELEASE).toBe(RELEASES[RELEASES.length - 1]);
		expect(CURRENT_RELEASE.version).toMatch(/^\d+\.\d+$/);
	});

	it('never shows to pending users', () => {
		expect(shouldShowWhatsNew(undefined, { role: 'pending' }, release)).toBe(false);
	});
});
