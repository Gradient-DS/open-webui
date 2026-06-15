// Shared error-message localization for the Cloud Sync admin panel.
//
// Provider connection-test endpoints (`/auth/test`) return a stable machine
// `reason` code alongside an English `detail` (a debug/console fallback). The
// frontend maps the `reason` to a fully-localized message so the toast a Dutch
// admin sees is fully Dutch — instead of a translated prefix wrapped around an
// English backend `detail` string. Used by every provider section (Confluence,
// TOPdesk, …) so the reason→message mapping lives in exactly one place.

import type { i18n as i18nType } from 'i18next';

// Stable connection-test reason codes returned by provider `/auth/test`
// endpoints. `(string & {})` keeps the type open to server-added codes while
// preserving literal autocomplete.
export type ConnectionReason =
	| 'ok'
	| 'missing_config'
	| 'auth_failed'
	| 'forbidden'
	| 'not_found'
	| 'rate_limited'
	| 'unavailable'
	| 'unreachable'
	| 'probe_failed'
	| 'error'
	| (string & {});

// Map a connection-test `reason` to a localized message.
//
// Known reasons → a translated message. Unknown/`error` → a generic translated
// message (the English `detail` is intentionally NOT surfaced in the toast so the
// message stays fully localized; callers should `console.error` the raw detail
// for debugging). The `detail` arg is only used as a last-resort fallback when the
// backend sent no `reason` at all (e.g. an older server).
export function connectionErrorMessage(
	i18n: i18nType,
	reason?: ConnectionReason | null,
	detail?: string | null
): string {
	const keys: Record<string, string> = {
		missing_config: 'The connection details are incomplete. Fill in all required fields.',
		auth_failed: 'Authentication failed. Check the credentials.',
		forbidden: 'Access denied — the account lacks permission.',
		not_found: 'Not found — check the URL.',
		rate_limited: 'The service is rate-limiting requests. Please try again shortly.',
		unavailable: 'The service is temporarily unavailable. Please try again shortly.',
		unreachable: 'The service could not be reached. Check the URL and network connectivity.',
		probe_failed: 'The connection could not be verified.'
	};
	if (reason && keys[reason]) return i18n.t(keys[reason]);
	// No reason at all (legacy backend) → fall back to whatever detail we got.
	if (!reason && detail) return detail;
	// Unknown reason / generic error → a localized catch-all.
	return i18n.t('The connection could not be verified.');
}
