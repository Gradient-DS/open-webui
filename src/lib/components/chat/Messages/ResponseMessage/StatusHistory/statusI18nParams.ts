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
	'count'
]);

/**
 * Returns every non-reserved field on a status event as a plain object
 * suitable for passing to `$i18n.t(key, params)`. Lets the agent backend
 * introduce new placeholders without a frontend code change — only a
 * translation-file entry.
 */
export function statusI18nParams(
	status: Record<string, unknown> | null | undefined
): Record<string, unknown> {
	if (!status) return {};
	const params: Record<string, unknown> = {};
	for (const [key, value] of Object.entries(status)) {
		if (!RESERVED_FIELDS.has(key)) {
			params[key] = value;
		}
	}
	return params;
}
