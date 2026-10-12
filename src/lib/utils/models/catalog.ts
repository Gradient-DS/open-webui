// [Gradient] soev-api catalog facts (info.meta.soev) as the picker shows them in v2 mode.
import { resolveLocalized } from '$lib/utils/localized';

export type CatalogMeta = {
	description?: Record<string, string> | null;
	vendor?: string | null;
	origin?: string | null;
	open_weights?: boolean | null;
	hosting?: { hosted_by?: string | null; region?: string | null } | null;
	lifecycle?: string | null;
	replaced_by?: string | null;
};

// Translated by the caller: i18n:parse only reads keys from .svelte and .js files.
export type CatalogLabels = {
	openWeights: string;
	hostedByIn: (host: string, region: string) => string;
	hostedBy: (host: string) => string;
	hostedIn: (region: string) => string;
};

type CatalogModel = {
	id: string;
	name?: string;
	info?: { meta?: { description?: string | null; soev?: CatalogMeta | null } | null } | null;
};

export const catalogMeta = (model?: CatalogModel | null): CatalogMeta | null =>
	model?.info?.meta?.soev ?? null;

// "<vendor> (<origin>) · open weights · hosted by <hosted_by> in <region>", leaving out unknown parts.
export const catalogLine = (
	meta: CatalogMeta | null | undefined,
	labels: CatalogLabels
): string => {
	if (!meta) return '';
	const parts: string[] = [];
	if (meta.vendor) parts.push(meta.origin ? `${meta.vendor} (${meta.origin})` : meta.vendor);
	if (meta.open_weights === true) parts.push(labels.openWeights);
	const host = meta.hosting?.hosted_by;
	const region = meta.hosting?.region;
	if (host && region) parts.push(labels.hostedByIn(host, region));
	else if (host) parts.push(labels.hostedBy(host));
	else if (region) parts.push(labels.hostedIn(region));
	return parts.join(' · ');
};

// The label of the model that replaces a superseded or deprecated one, if offered.
export const replacementLabel = (
	meta: CatalogMeta | null | undefined,
	models: CatalogModel[]
): string => {
	const id = meta?.replaced_by;
	if (!id || meta?.lifecycle === 'active') return '';
	return models.find((model) => model.id === id)?.name || id;
};

// Show the catalog description in the UI language unless an admin wrote their own.
export const localizeCatalogDescriptions = <T extends CatalogModel>(
	models: T[],
	lang: string | null | undefined
): T[] =>
	models.map((model) => {
		const localized = catalogMeta(model)?.description;
		const meta = model.info?.meta;
		if (!localized || !meta) return model;
		const current = meta.description;
		if (current && !Object.values(localized).includes(current)) return model;
		return {
			...model,
			info: {
				...model.info,
				meta: { ...meta, description: resolveLocalized(localized, lang, 'en') }
			}
		};
	});

// The label to show when a fallback answered instead of the requested model.
export const answeredByLabel = (
	answered: string | null | undefined,
	requested: string | null | undefined,
	models: CatalogModel[]
): string => {
	if (!answered || answered === requested) return '';
	return models.find((model) => model.id === answered)?.name || answered;
};
