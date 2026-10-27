// [Gradient] The caller's data sources (`utils/dataSources.ts`), shared by the + menu and the composer bar.
import { get, writable, type Writable } from 'svelte/store';

import { getDataSources, type DataSource } from '$lib/apis/dataSources';

/** Null until loaded. */
export const dataSources: Writable<DataSource[] | null> = writable(null);

let loading: Promise<void> | null = null;

/** Loads (or reloads) the list; one that cannot be read keeps the last list, or reads as empty. */
export function loadDataSources(token: string): Promise<void> {
	loading ??= getDataSources(token)
		.then((items) => dataSources.set(items))
		.catch(() => dataSources.set(get(dataSources) ?? []))
		.finally(() => (loading = null));
	return loading;
}
