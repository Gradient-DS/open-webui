import { describe, it, expect } from 'vitest';
import {
	answeredByLabel,
	catalogLine,
	localizeCatalogDescriptions,
	replacementLabel
} from './catalog';
import { defaultLLMId, type AssistantModel } from '$lib/utils/assistants';

const t = {
	openWeights: 'open weights',
	hostedByIn: (host: string, region: string) => `hosted by ${host} in ${region}`,
	hostedBy: (host: string) => `hosted by ${host}`,
	hostedIn: (region: string) => `hosted in ${region}`
};

const glm = {
	id: 'glm-5-3',
	name: 'GLM 5.3',
	owned_by: 'soev',
	info: {
		meta: {
			description: 'Strong general model',
			soev: {
				description: { nl: 'Sterk algemeen model', en: 'Strong general model' },
				vendor: 'Zhipu AI',
				origin: 'CN',
				open_weights: true,
				hosting: { hosted_by: 'Nebul', region: 'NL' },
				lifecycle: 'active',
				replaced_by: null
			}
		}
	}
};

describe('catalogLine', () => {
	it('joins vendor, origin, open weights and hosting', () => {
		expect(catalogLine(glm.info.meta.soev, t)).toBe(
			'Zhipu AI (CN) · open weights · hosted by Nebul in NL'
		);
	});

	it('leaves out unknown parts', () => {
		expect(catalogLine({ vendor: 'Google', open_weights: null, hosting: null }, t)).toBe('Google');
		expect(catalogLine({ origin: 'US', open_weights: false, hosting: { region: 'EU' } }, t)).toBe(
			'hosted in EU'
		);
		expect(catalogLine({ hosting: { hosted_by: 'Nebul' } }, t)).toBe('hosted by Nebul');
		expect(catalogLine(null, t)).toBe('');
	});
});

describe('replacementLabel', () => {
	it('names the replacement of a superseded model by its label', () => {
		expect(replacementLabel({ lifecycle: 'superseded', replaced_by: 'glm-5-3' }, [glm])).toBe(
			'GLM 5.3'
		);
		expect(replacementLabel({ lifecycle: 'deprecated', replaced_by: 'gone' }, [glm])).toBe('gone');
	});

	it('is empty for active models', () => {
		expect(replacementLabel({ lifecycle: 'active', replaced_by: 'glm-5-3' }, [glm])).toBe('');
	});
});

describe('localizeCatalogDescriptions', () => {
	it('picks the UI language', () => {
		const [nl] = localizeCatalogDescriptions([glm], 'nl-NL');
		expect(nl.info.meta.description).toBe('Sterk algemeen model');
		expect(localizeCatalogDescriptions([glm], 'de-DE')[0].info.meta.description).toBe(
			'Strong general model'
		);
	});

	it("keeps an admin's own description", () => {
		const edited = { ...glm, info: { meta: { ...glm.info.meta, description: 'Ours' } } };
		expect(localizeCatalogDescriptions([edited], 'nl-NL')[0].info.meta.description).toBe('Ours');
	});
});

describe('answeredByLabel', () => {
	it('shows a fallback only', () => {
		expect(answeredByLabel('glm-5-3', 'gemma-4-31b', [glm])).toBe('GLM 5.3');
		expect(answeredByLabel('glm-5-3', 'glm-5-3', [glm])).toBe('');
		expect(answeredByLabel(undefined, 'glm-5-3', [glm])).toBe('');
	});
});

describe('default selection with a catalog default', () => {
	const gemma = { ...glm, id: 'gemma-4-31b', name: 'Gemma' };
	const models = [gemma, glm] as AssistantModel[];

	it('falls back to the default served as default_models', () => {
		expect(defaultLLMId(models, ['glm-5-3'])).toBe('glm-5-3');
	});

	it("keeps a user's saved model ahead of it", () => {
		expect(defaultLLMId(models, ['gemma-4-31b', 'glm-5-3'])).toBe('gemma-4-31b');
	});

	it('skips a saved model the catalog no longer offers', () => {
		expect(defaultLLMId(models, ['zai-org/GLM-5.3', 'glm-5-3'])).toBe('glm-5-3');
	});
});
