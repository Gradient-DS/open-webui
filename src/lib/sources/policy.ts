import { derived, writable, type Writable } from 'svelte/store';
import { getPolicy, type SyncPolicy } from '$lib/apis/cloudSync';
import { providers } from './registry';

export const emptyPolicy: SyncPolicy = {
	providers_enabled: [],
	scope_shapes_allowed: {},
	min_cadence_minutes: null,
	default_cadence_minutes: null
};

export const sourcePolicy: Writable<SyncPolicy | null> = writable(null);
export const enabledProviders = derived(sourcePolicy, (policy) =>
	Object.values(providers).filter((provider) => policy?.providers_enabled.includes(provider.kind))
);

let policyToken: string | undefined;
let pending: Promise<SyncPolicy> | undefined;

export function loadSourcePolicy(token: string): Promise<SyncPolicy> {
	if (pending && policyToken === token) return pending;
	policyToken = token;
	sourcePolicy.set(null);
	const request = Promise.resolve()
		.then(() => getPolicy(token))
		.catch(() => emptyPolicy)
		.then((policy) => {
			if (pending === request) sourcePolicy.set(policy);
			return policy;
		});
	pending = request;
	return request;
}
