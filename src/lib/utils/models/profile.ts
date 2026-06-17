import modelProfiles from '$lib/data/model-profiles.json';

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
	origin?: ModelOrigin; // where the model comes from, shown as a flag
	local?: boolean; // hosted locally; when false a data-warning sign is shown
	eco?: boolean; // show the green leaf badge
};

/** Region a model originates from. Shown as a flag in the dropdown. */
export type ModelOrigin = 'EU' | 'US' | 'CN';

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

/**
 * Display label for each model origin. The flag artwork lives in
 * Flag.svelte (inline SVG); this just maps the origin to its i18n label,
 * used for the tooltip and accessible name.
 */
export const ORIGIN_META: Record<ModelOrigin, { labelKey: string }> = {
	EU: { labelKey: 'Europe (EU)' },
	US: { labelKey: 'United States' },
	CN: { labelKey: 'China' }
};

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
