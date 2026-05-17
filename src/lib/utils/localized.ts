import type { LocalizedString } from '$lib/types';

const DEFAULT_FALLBACK_LANG = 'en-US';

// Resolve a `LocalizedString` to a plain string for the given language.
// Falls back to the base language ("nl" for "nl-NL"), then `fallbackLang`,
// then any non-empty entry. Plain strings pass through (legacy data).
export const resolveLocalized = (
	value: LocalizedString | null | undefined,
	lang: string | null | undefined,
	fallbackLang: string = DEFAULT_FALLBACK_LANG
): string => {
	if (value == null) return '';
	if (typeof value === 'string') return value;
	if (typeof value !== 'object') return '';

	const activeLang = lang ?? fallbackLang;

	const exact = value[activeLang];
	if (exact) return exact;

	const baseLang = activeLang.split('-')[0];
	const baseMatchKey = Object.keys(value).find((k) => k.split('-')[0] === baseLang);
	if (baseMatchKey && value[baseMatchKey]) return value[baseMatchKey];

	if (value[fallbackLang]) return value[fallbackLang];

	for (const k of Object.keys(value)) {
		if (value[k]) return value[k];
	}

	return '';
};

// True if the value has any localized content (or a non-empty legacy string).
export const hasLocalizedContent = (value: LocalizedString | null | undefined): boolean => {
	if (value == null) return false;
	if (typeof value === 'string') return value.length > 0;
	if (typeof value !== 'object') return false;
	return Object.values(value).some((v) => typeof v === 'string' && v.length > 0);
};

// Normalize a value to the object form, seeding legacy strings under `seedLang`.
// Used by admin editors so they always work against an object.
export const toLocalizedObject = (
	value: LocalizedString | null | undefined,
	seedLang: string = DEFAULT_FALLBACK_LANG
): Record<string, string> => {
	if (value == null) return {};
	if (typeof value === 'string') {
		return value.length > 0 ? { [seedLang]: value } : {};
	}
	if (typeof value !== 'object') return {};
	return { ...value };
};
