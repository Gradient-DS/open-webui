/**
 * Mapping between a cloud KB's provider sources (meta[metaKey].sources[]) and
 * the knowledge_directory rows that materialize them (P2-8 decision 2).
 *
 * Folder-like sources get one root directory each, recorded as
 * ``sources[].root_directory_id`` by the backend when the chain is first
 * materialized. File-type sources have NO wrapper directory — their files sit
 * at the KB root — so they never appear in this map.
 */

export interface ProviderSource {
	item_id: string;
	name?: string;
	type?: string;
	root_directory_id?: string;
	[key: string]: unknown;
}

/**
 * Index a KB's provider sources by their materialized root directory id.
 * Drives the remove-source affordance + sync spinner on source-root
 * directory rows: a directory row whose id is in this map IS a source root.
 */
export function sourceByRootDirectoryId(
	sources: ProviderSource[] | null | undefined
): Map<string, ProviderSource> {
	const map = new Map<string, ProviderSource>();
	for (const source of sources ?? []) {
		if (source?.root_directory_id) {
			map.set(source.root_directory_id, source);
		}
	}
	return map;
}
