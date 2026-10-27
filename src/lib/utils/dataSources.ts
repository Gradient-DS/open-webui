// [Gradient] Data sources: tenant-wide platform collections (`ds:` keys) a user switches on per
// chat. On means the agent may search it (`auto`); the switched-on ids travel in
// `features.data_sources` and persist with the other composer choices.
import type { DataSource } from '$lib/apis/dataSources';

export const DATA_SOURCE_PIN_PREFIX = 'data_source:';

/** A saved selection, cleaned: `ds:` ids only, each once, in stored order. */
export function dataSourceIds(value: unknown): string[] {
	return Array.isArray(value)
		? [
				...new Set(
					value.filter(
						(id): id is string => typeof id === 'string' && id.startsWith('ds:') && id.length > 3
					)
				)
			]
		: [];
}

/** The ids sent with a turn: the switched-on ones that are still listed (all of them before the list loads). */
export function requestedDataSources(
	selected: readonly string[],
	available: readonly DataSource[] | null
): string[] {
	const ids = dataSourceIds(selected);
	return available === null
		? ids
		: ids.filter((id) => available.some((source) => source.id === id));
}

/** Switches one data source on or off. */
export function toggleDataSource(selected: readonly string[], id: string): string[] {
	return selected.includes(id) ? selected.filter((item) => item !== id) : [...selected, id];
}
