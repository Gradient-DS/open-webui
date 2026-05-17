import type { i18n as I18nType } from 'i18next';

export interface ToastSegments {
	added?: number;
	updated?: number;
	// `unchanged` isn't rendered as its own segment — it's only used to
	// detect "no changes" when every other segment is zero. Keeping it on
	// the input shape makes the call site read like a direct payload copy.
	unchanged?: number;
	failed?: number;
	removed?: number;
}

export type ToastVariant = 'success' | 'warning' | 'error' | 'info';

export interface ToastResult {
	variant: ToastVariant;
	message: string;
}

export function buildSyncToast(
	i18n: I18nType,
	label: string | null,
	s: ToastSegments
): ToastResult {
	const segments: string[] = [];
	if (s.added && s.added > 0) {
		segments.push(i18n.t('Added {{count}}', { count: s.added }));
	}
	if (s.updated && s.updated > 0) {
		segments.push(i18n.t('Updated {{count}}', { count: s.updated }));
	}
	if (s.failed && s.failed > 0) {
		segments.push(i18n.t('{{count}} failed', { count: s.failed }));
	}
	if (s.removed && s.removed > 0) {
		segments.push(i18n.t('Removed {{count}}', { count: s.removed }));
	}

	let body: string;
	if (segments.length === 0) {
		body = i18n.t('No changes');
	} else {
		body = segments.join(', ');
	}

	const message = label ? `${label}: ${body}` : body;

	const success = (s.added ?? 0) + (s.updated ?? 0) + (s.removed ?? 0);
	const failed = s.failed ?? 0;
	let variant: ToastVariant;
	if (failed > 0 && success === 0) {
		variant = 'error';
	} else if (failed > 0) {
		variant = 'warning';
	} else if (segments.length === 0) {
		variant = 'info';
	} else {
		variant = 'success';
	}

	return { variant, message };
}
