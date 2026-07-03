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

/**
 * A user-facing string that may be localized per UI locale. A plain string is
 * locale-agnostic (shown as-is in every language). An object is keyed by locale
 * code (e.g. "en-US", "nl-NL"); resolveLocalizedText picks the active locale with
 * fallbacks. Used for the free-text profile fields supplied by the deployment.
 */
export type LocalizedText = string | Record<string, string>;

/**
 * Profile as supplied by the deployment (Helm MODEL_PROFILES). Same as ModelProfile
 * except the free-text fields (info, bestFor, name) may be localized. Flattened to a
 * ModelProfile (plain strings for the active locale) by resolveModelProfile.
 */
export type RawModelProfile = {
	name?: LocalizedText;
	bestFor?: LocalizedText;
	info?: LocalizedText;
	speed?: number;
	quality?: number;
	origin?: ModelOrigin;
	hosting?: string;
	eco?: boolean;
};

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

export type ProfileRule = {
	match: string;
	profile?: RawModelProfile;
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
 * Resolve the per-deployment hosting/datacenter label for a model from the
 * MODEL_HOSTING rules supplied by the backend (Helm env, surfaced on /api/config
 * as `model_hosting`). Rules use the SAME matching convention as the model
 * profiles (matchesPattern): case-insensitive substring, or an anchored glob when
 * the pattern contains '*', against the model id OR display name; later rules win.
 * This is intentionally deployment-specific — the same model id can be hosted in a
 * different datacenter for a different client — so it is authoritative over both the
 * baked profile `hosting` and the live connection host. Returns the last matching
 * rule's (non-empty) hosting label, or undefined when nothing matches / no rules are
 * configured, so callers fall back gracefully.
 */
export function hostingFromDeployment(
	rules: { match: string; hosting: string }[] | undefined,
	id: string,
	name: string
): string | undefined {
	if (!rules?.length) return undefined;

	let hosting: string | undefined;
	for (const rule of rules) {
		if (rule?.match && rule?.hosting && matchesPattern(rule.match, id, name)) {
			hosting = rule.hosting; // later-wins: keep the last matching label
		}
	}
	return hosting;
}

/**
 * Pick the string for the active locale from a LocalizedText. Plain strings pass
 * through unchanged. For an object: exact locale match (e.g. "nl-NL") → any key
 * sharing the language prefix (e.g. "nl") → "en-US"/"en" → the first value.
 * Returns undefined for a nullish value or an empty object.
 */
export function resolveLocalizedText(
	value: LocalizedText | undefined,
	locale: string
): string | undefined {
	if (value === undefined || value === null) return undefined;
	if (typeof value === 'string') return value;

	const keys = Object.keys(value);
	if (!keys.length) return undefined;

	const loc = (locale ?? '').toLowerCase();
	const lang = loc.split('-')[0];

	const exact = keys.find((k) => k.toLowerCase() === loc);
	if (exact) return value[exact];

	const byLang = keys.find((k) => k.toLowerCase().split('-')[0] === lang && lang !== '');
	if (byLang) return value[byLang];

	const en =
		keys.find((k) => k.toLowerCase() === 'en-us') ??
		keys.find((k) => k.toLowerCase().split('-')[0] === 'en');
	if (en) return value[en];

	return value[keys[0]];
}

/**
 * Resolve the effective profile for a model from the deployment-supplied rules
 * (Helm MODEL_PROFILES, surfaced on /api/config as `model_profiles`). Start from
 * every matching rule (later rules override earlier ones field-by-field, so specific
 * beats generic), flattening localized free-text (info/bestFor/name) to the active
 * `locale`, then apply the admin's per-model override (model.info.meta.profile)
 * field-by-field. Blank values (undefined / null / '') fall through. No rules, or a
 * model matching no rule, yields {} — the model renders plain (just its name).
 */
export function resolveModelProfile(
	model: {
		id?: string;
		name?: string;
		info?: { meta?: { profile?: ModelProfile } };
	},
	profileRules: ProfileRule[] | undefined,
	locale: string
): ModelProfile {
	const id = model?.id ?? '';
	const name = model?.name ?? '';

	let base: ModelProfile = {};
	for (const rule of profileRules ?? []) {
		if (!rule.profile) continue; // skip comment-only entries
		if (!matchesPattern(rule.match, id, name)) continue;

		// Flatten this rule onto the base, copying only the keys it actually sets so
		// later-wins stays field-by-field (an absent key must not clobber an earlier
		// match). Localized free-text is resolved to the active locale here.
		const p = rule.profile;
		const flat: ModelProfile = {};
		if (p.speed !== undefined) flat.speed = p.speed;
		if (p.quality !== undefined) flat.quality = p.quality;
		if (p.origin !== undefined) flat.origin = p.origin;
		if (p.hosting !== undefined) flat.hosting = p.hosting;
		if (p.eco !== undefined) flat.eco = p.eco;
		const name_ = resolveLocalizedText(p.name, locale);
		if (name_ !== undefined) flat.name = name_;
		const bestFor = resolveLocalizedText(p.bestFor, locale);
		if (bestFor !== undefined) flat.bestFor = bestFor;
		const info = resolveLocalizedText(p.info, locale);
		if (info !== undefined) flat.info = info;

		base = { ...base, ...flat };
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
