import modelProfiles from '$lib/data/model-profiles.json';
import hostingProviders from '$lib/data/hosting-providers.json';

/**
 * Plain-language profile shown in the model dropdown to help users understand a
 * model at a glance. Admin-entered values (model.info.meta.profile) take
 * precedence; anything left blank falls back to the defaults file, matched by
 * model family. See src/lib/data/model-profiles.json.
 */
export type ModelProfile = {
	name?: string; // friendly short name, e.g. "Gemma" (falls back to the model label)
	bestFor?: string; // free-text label, e.g. "Goed voor alledaagse taken"
	info?: string; // free-text shown on hover behind the (i) icon
	speed?: number; // 1-3
	quality?: number; // 1-3
	origin?: ModelOrigin; // where the model comes from (not rendered; mention it in `info` instead)
	hosting?: string; // who/where the model is hosted, e.g. "Nebul (NL)"; shown in the info tooltip.
	// A data-sovereignty warning is shown automatically when the hosting flag is not NL/EU.
	eco?: boolean; // show the green leaf badge
};

/** Region a model originates from. Not rendered; kept for data/back-compat. */
export type ModelOrigin = 'EU' | 'US' | 'CN';

/** Country/region codes that have flag artwork in Flag.svelte. */
export type FlagCode = 'EU' | 'US' | 'CN' | 'NL';

export const FLAG_CODES: FlagCode[] = ['EU', 'US', 'CN', 'NL'];

/**
 * Parse a free-text hosting value like "Nebule (NL)" into a display label and an
 * optional flag code. The label is the text before the parentheses ("Nebule");
 * the parenthetical is treated as a country code and rendered as a flag when it
 * matches one we have artwork for (see FLAG_CODES). If there are no parentheses
 * the whole string is the label and no flag is shown.
 */
type HostingRule = {
	match: string;
	hosting: string;
	_comment?: string;
};

const hostingRules = hostingProviders as HostingRule[];

/**
 * Map an upstream connection hostname (the model's `connection_host`, derived from
 * the LiteLLM/OpenAI api_base on the backend) to a clean hosting label such as
 * "Nebul (NL)". Rules in hosting-providers.json are matched top-to-bottom by
 * case-insensitive substring of the hostname; the first match wins. Returns
 * undefined when there's no host or no rule matches, so callers fall back to the
 * static `hosting` value from model-profiles.json.
 */
export function hostingFromHost(host?: string): string | undefined {
	const h = (host ?? '').toLowerCase().trim();
	if (!h) return undefined;

	for (const rule of hostingRules) {
		if (rule.match && h.includes(rule.match.toLowerCase())) {
			return rule.hosting || undefined;
		}
	}
	return undefined;
}

export function parseHosting(hosting?: string): { label: string; flag?: FlagCode } | undefined {
	const raw = (hosting ?? '').trim();
	if (!raw) return undefined;

	const m = raw.match(/^(.*?)\s*\(([^)]+)\)\s*$/);
	if (!m) return { label: raw };

	const label = m[1].trim();
	const code = m[2].trim().toUpperCase() as FlagCode;
	const flag = FLAG_CODES.includes(code) ? code : undefined;
	return { label: label || raw, flag };
}

type ProfileRule = {
	match: string;
	profile: ModelProfile;
	_comment?: string;
};

/**
 * The dot-meter axes, in display order, with their colours. Shared by the
 * per-row meters (ModelProfile.svelte) and the dropdown legend
 * (ModelProfileLegend.svelte) so the two can never drift apart.
 * `labelKey` is passed through i18n in the components.
 * Use literal Tailwind classes here so the JIT compiler picks them up.
 */
export const PROFILE_AXES = [
	{
		key: 'quality',
		labelKey: 'Quality',
		descKey: 'how strong the model is at complex tasks',
		color: 'text-indigo-500'
	},
	{
		key: 'speed',
		labelKey: 'Speed',
		descKey: 'how fast it responds',
		color: 'text-amber-500'
	}
] as const;

export type ProfileAxisKey = (typeof PROFILE_AXES)[number]['key'];

const rules = modelProfiles as ProfileRule[];

function escapeRegExp(s: string): string {
	return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function matchesPattern(pattern: string, id: string, name: string): boolean {
	if (!pattern || pattern === '*') return true;

	const p = pattern.toLowerCase();
	const lid = id.toLowerCase();
	const lname = name.toLowerCase();

	if (p.includes('*')) {
		const regex = new RegExp('^' + p.split('*').map(escapeRegExp).join('.*') + '$');
		return regex.test(lid) || regex.test(lname);
	}

	return lid.includes(p) || lname.includes(p);
}

/**
 * Resolve the effective profile for a model: start from every matching defaults
 * rule (later rules in the file override earlier ones, so specific beats
 * generic), then apply the admin's per-model overrides field-by-field. Blank
 * admin values (undefined / null / '') fall through to the default.
 */
export function resolveModelProfile(model: {
	id?: string;
	name?: string;
	info?: { meta?: { profile?: ModelProfile } };
}): ModelProfile {
	const id = model?.id ?? '';
	const name = model?.name ?? '';

	let base: ModelProfile = {};
	for (const rule of rules) {
		if (matchesPattern(rule.match, id, name)) {
			base = { ...base, ...rule.profile };
		}
	}

	const override = model?.info?.meta?.profile ?? {};
	const merged: ModelProfile = { ...base };
	for (const [key, value] of Object.entries(override)) {
		if (value !== undefined && value !== null && value !== '') {
			merged[key as keyof ModelProfile] = value as never;
		}
	}

	return merged;
}
