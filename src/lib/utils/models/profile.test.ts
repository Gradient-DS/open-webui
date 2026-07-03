import { describe, it, expect } from 'vitest';
import {
	resolveModelProfile,
	resolveLocalizedText,
	hostingFromDeployment,
	parseHosting
} from './profile';

// hostingFromDeployment exercises the shared matchesPattern() convention with
// caller-supplied rules, so it's the cleanest surface to assert the matching rules
// that both MODEL_HOSTING and model-profiles.json rely on.
describe('hostingFromDeployment — matching convention', () => {
	it('matches by case-insensitive substring of the model id', () => {
		const rules = [{ match: 'gemma', hosting: 'Nebul (NL)' }];
		expect(hostingFromDeployment(rules, 'gemma-4-31B-it', '')).toBe('Nebul (NL)');
		expect(hostingFromDeployment(rules, 'GEMMA-4-26B', '')).toBe('Nebul (NL)');
	});

	it('matches against the display name as well as the id', () => {
		const rules = [{ match: 'claude', hosting: 'Anthropic (US)' }];
		expect(hostingFromDeployment(rules, 'anthropic/some-id', 'Claude Sonnet 4.6')).toBe(
			'Anthropic (US)'
		);
	});

	it('a bare "gpt" substring rule WRONGLY catches gpt-oss (the collision globs avoid)', () => {
		const rules = [{ match: 'gpt', hosting: 'Azure (EU)' }];
		// documents why provider-family rules must use a glob, not a substring
		expect(hostingFromDeployment(rules, 'openai/gpt-oss-120b', 'gpt-oss-120b')).toBe('Azure (EU)');
	});

	it('a "gpt-4*" glob matches gpt-4.1 but NOT openai/gpt-oss-120b', () => {
		const rules = [{ match: 'gpt-4*', hosting: 'Azure (EU)' }];
		expect(hostingFromDeployment(rules, 'gpt-4.1', 'gpt-4.1')).toBe('Azure (EU)');
		expect(hostingFromDeployment(rules, 'openai/gpt-oss-120b', 'gpt-oss-120b')).toBeUndefined();
	});

	it('later rules win, so a broad "*" default can be overridden by a specific rule', () => {
		const rules = [
			{ match: '*', hosting: 'Nebul (NL)' },
			{ match: 'claude', hosting: 'Anthropic (US)' }
		];
		expect(hostingFromDeployment(rules, 'claude-sonnet-4-6', 'Claude')).toBe('Anthropic (US)');
		expect(hostingFromDeployment(rules, 'gemma-4-31B-it', 'Gemma')).toBe('Nebul (NL)');
	});

	it('returns undefined for no rules, empty rules, or no match', () => {
		expect(hostingFromDeployment(undefined, 'x', 'y')).toBeUndefined();
		expect(hostingFromDeployment([], 'x', 'y')).toBeUndefined();
		expect(
			hostingFromDeployment([{ match: 'mistral', hosting: 'Nebul (NL)' }], 'gemma', 'Gemma')
		).toBeUndefined();
	});

	it('skips matching rules with an empty hosting label', () => {
		const rules = [
			{ match: '*', hosting: 'Nebul (NL)' },
			{ match: 'claude', hosting: '' }
		];
		// the empty-hosting claude rule does not clobber the broad default
		expect(hostingFromDeployment(rules, 'claude-sonnet', 'Claude')).toBe('Nebul (NL)');
	});
});

describe('resolveModelProfile — deployment-supplied rules', () => {
	// Mirrors the shape delivered by /api/config.model_profiles (Helm MODEL_PROFILES).
	// Free-text fields are localized {en-US, nl-NL}; meters are plain numbers.
	const NL = 'nl-NL';
	const RULES = [
		{
			match: 'gpt-oss',
			profile: {
				bestFor: { 'en-US': 'Reasoning (strong in English)', 'nl-NL': 'Redeneren (Engels sterk)' },
				speed: 2,
				quality: 3
			}
		},
		{
			match: 'gpt-4*',
			profile: {
				bestFor: { 'en-US': 'Versatile & code', 'nl-NL': 'Veelzijdig & code' },
				speed: 2,
				quality: 3
			}
		},
		{
			match: 'gemini*',
			profile: {
				bestFor: {
					'en-US': 'Fast questions (multimodal)',
					'nl-NL': 'Snelle vragen (multimodaal)'
				},
				speed: 3,
				quality: 2
			}
		},
		{
			match: 'gemini*pro*',
			profile: {
				bestFor: {
					'en-US': 'Complex reasoning (multimodal)',
					'nl-NL': 'Complex redeneren (multimodaal)'
				},
				speed: 1,
				quality: 3
			}
		},
		{
			match: 'gemma-4-31',
			profile: {
				bestFor: { 'en-US': 'Versatile & multilingual', 'nl-NL': 'Veelzijdig & meertalig' },
				speed: 2,
				quality: 3
			}
		}
	];

	it('resolves gpt-oss to its reasoning profile, not the gpt-4 profile (collision check)', () => {
		const p = resolveModelProfile({ id: 'openai/gpt-oss-120b', name: 'gpt-oss-120b' }, RULES, NL);
		expect(p.bestFor).toBe('Redeneren (Engels sterk)');
		expect(p.quality).toBe(3);
	});

	it('resolves gpt-4.1 to the gpt-4 profile via the gpt-4* glob', () => {
		const p = resolveModelProfile({ id: 'gpt-4.1', name: 'gpt-4.1' }, RULES, NL);
		expect(p.bestFor).toBe('Veelzijdig & code');
	});

	it('applies later-wins so gemini *pro* overrides the generic gemini profile', () => {
		const flash = resolveModelProfile(
			{ id: 'gemini-2.5-flash', name: 'gemini-2.5-flash' },
			RULES,
			NL
		);
		expect(flash.quality).toBe(2);
		expect(flash.speed).toBe(3);

		const pro = resolveModelProfile({ id: 'gemini-2.5-pro', name: 'gemini-2.5-pro' }, RULES, NL);
		expect(pro.quality).toBe(3);
		expect(pro.speed).toBe(1);
	});

	it('selects the active locale for free-text fields, falling back to en-US', () => {
		const en = resolveModelProfile({ id: 'gemma-4-31B-it', name: 'Gemma' }, RULES, 'en-US');
		expect(en.bestFor).toBe('Versatile & multilingual');

		const nl = resolveModelProfile({ id: 'gemma-4-31B-it', name: 'Gemma' }, RULES, 'nl-NL');
		expect(nl.bestFor).toBe('Veelzijdig & meertalig');

		// An unknown locale falls back to en-US.
		const fr = resolveModelProfile({ id: 'gemma-4-31B-it', name: 'Gemma' }, RULES, 'fr-FR');
		expect(fr.bestFor).toBe('Versatile & multilingual');
	});

	it('returns an empty profile when no rules are supplied (deployment unset)', () => {
		expect(resolveModelProfile({ id: 'gemma-4-31B-it', name: 'Gemma' }, [], NL)).toEqual({});
		expect(resolveModelProfile({ id: 'gemma-4-31B-it', name: 'Gemma' }, undefined, NL)).toEqual({});
	});

	it('returns an empty profile for a model matching no rule (no catch-all)', () => {
		expect(
			resolveModelProfile({ id: 'my-custom-model', name: 'My Custom Model' }, RULES, NL)
		).toEqual({});
	});

	it('lets an admin meta.profile override the deployment rule field-by-field', () => {
		const p = resolveModelProfile(
			{
				id: 'gemma-4-31B-it',
				name: 'Gemma',
				info: { meta: { profile: { bestFor: 'Custom label', quality: 1 } } }
			},
			RULES,
			NL
		);
		expect(p.bestFor).toBe('Custom label');
		expect(p.quality).toBe(1);
		// fields the admin left blank still fall through to the deployment rule
		expect(p.speed).toBe(2);
	});
});

describe('resolveLocalizedText — locale selection', () => {
	const TEXT = { 'en-US': 'English', 'nl-NL': 'Nederlands' };

	it('passes a plain string through unchanged (locale-agnostic)', () => {
		expect(resolveLocalizedText('Plain', 'nl-NL')).toBe('Plain');
		expect(resolveLocalizedText('Plain', 'en-US')).toBe('Plain');
	});

	it('picks the exact locale match', () => {
		expect(resolveLocalizedText(TEXT, 'nl-NL')).toBe('Nederlands');
		expect(resolveLocalizedText(TEXT, 'en-US')).toBe('English');
	});

	it('matches by language prefix when there is no exact locale', () => {
		expect(resolveLocalizedText(TEXT, 'nl')).toBe('Nederlands');
		expect(resolveLocalizedText({ en: 'E', nl: 'N' }, 'nl-BE')).toBe('N');
	});

	it('falls back to en-US/en for an unknown locale', () => {
		expect(resolveLocalizedText(TEXT, 'fr-FR')).toBe('English');
		expect(resolveLocalizedText({ en: 'E', de: 'D' }, 'fr')).toBe('E');
	});

	it('falls back to the first value when no en is present', () => {
		expect(resolveLocalizedText({ de: 'D', fr: 'F' }, 'es')).toBe('D');
	});

	it('returns undefined for nullish or empty values', () => {
		expect(resolveLocalizedText(undefined, 'nl-NL')).toBeUndefined();
		expect(resolveLocalizedText({}, 'nl-NL')).toBeUndefined();
	});
});

describe('hosting resolution precedence (mirrors ModelItem.svelte)', () => {
	// The component resolves: deployment rule ?? live host ?? baked profile.hosting.
	const resolveHosting = (
		rules: { match: string; hosting: string }[] | undefined,
		id: string,
		name: string,
		bakedHosting?: string
	) => parseHosting(hostingFromDeployment(rules, id, name) ?? bakedHosting);

	it('the deployment rule wins over a baked hosting value', () => {
		const out = resolveHosting(
			[{ match: '*', hosting: 'Nebul (NL)' }],
			'claude-sonnet',
			'Claude',
			'Legacy (US)'
		);
		expect(out).toEqual({ label: 'Nebul', flag: 'NL' });
	});

	it('falls back to the baked hosting when no deployment rule matches', () => {
		const out = resolveHosting([{ match: 'mistral', hosting: 'Nebul (NL)' }], 'claude', 'Claude', 'Anthropic (US)');
		expect(out).toEqual({ label: 'Anthropic', flag: 'US' });
	});
});

describe('parseHosting — flag extraction', () => {
	it('extracts a supported country code as a flag', () => {
		expect(parseHosting('Anthropic (US)')).toEqual({ label: 'Anthropic', flag: 'US' });
		expect(parseHosting('Intermax litellm (NL)')).toEqual({ label: 'Intermax litellm', flag: 'NL' });
		expect(parseHosting('Azure (EU)')).toEqual({ label: 'Azure', flag: 'EU' });
	});

	it('renders a label with no flag when there is no/unknown country code', () => {
		expect(parseHosting('Some Datacenter')).toEqual({ label: 'Some Datacenter' });
		expect(parseHosting('Host (XX)')).toEqual({ label: 'Host', flag: undefined });
	});

	it('returns undefined for empty input', () => {
		expect(parseHosting('')).toBeUndefined();
		expect(parseHosting(undefined)).toBeUndefined();
	});
});
