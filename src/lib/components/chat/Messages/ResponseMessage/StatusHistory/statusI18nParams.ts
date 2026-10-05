/**
 * Reserved structural fields on a status event — these are consumed by
 * StatusItem.svelte's per-action branches and must NOT be forwarded as
 * i18next interpolation params.
 */
const RESERVED_FIELDS = new Set([
	'description',
	'action',
	'done',
	'hidden',
	'urls',
	'items',
	'queries',
	'query',
	'count',
	'mail_options'
]);

/**
 * Returns every non-reserved field on a status event as a plain object
 * suitable for passing to `$i18n.t(key, params)`. Lets the agent backend
 * introduce new placeholders without a frontend code change — only a
 * translation-file entry.
 */
export function statusI18nParams(
	status: Record<string, unknown> | null | undefined,
	translate: (key: string) => string = (key) => key
): Record<string, unknown> {
	if (!status) return {};
	const params: Record<string, unknown> = {};
	for (const [key, value] of Object.entries(status)) {
		if (!RESERVED_FIELDS.has(key)) {
			params[key] = value;
		}
	}
	if (status.action === 'search_mail' && Array.isArray(status.mail_options)) {
		params.options = status.mail_options
			.map(({ label, value }) => translate(label) + (value ? `: ${value}` : ''))
			.join('; ');
	}
	return params;
}
