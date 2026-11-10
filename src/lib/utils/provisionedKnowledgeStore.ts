// The composer and menu share the caller's provisioned knowledge list.
import { get, writable, type Writable } from 'svelte/store';

import { getProvisionedKnowledge, type ProvisionedKnowledge } from '$lib/apis/provisionedKnowledge';

/** Null until loaded. */
export const provisionedKnowledge: Writable<ProvisionedKnowledge[] | null> = writable(null);

let loading: Promise<void> | null = null;

/** Loads (or reloads) the list; one that cannot be read keeps the last list, or reads as empty. */
export function loadProvisionedKnowledge(token: string): Promise<void> {
	loading ??= getProvisionedKnowledge(token)
		.then((items) => provisionedKnowledge.set(items))
		.catch(() => provisionedKnowledge.set(get(provisionedKnowledge) ?? []))
		.finally(() => (loading = null));
	return loading;
}
