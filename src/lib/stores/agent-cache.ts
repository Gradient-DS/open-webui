// [Gradient] Shared session cache of the visible-agents list.
//
// Promoted out of AgentBadge.svelte's module context so AgentSelector and
// AgentBadge share one fetch and one cache: looking up an agent for a given
// slug is synchronous after the first load, which is what kills the
// fetch-during-switch flicker (raw slug briefly visible while a network
// round-trip resolves).
//
// ``null`` = not yet loaded; an empty array = loaded but no rows.
import { writable } from 'svelte/store';
import { listVisibleAgents, type AgentConfigUserResponse } from '$lib/apis/agent-configs';

export const agentsCache = writable<AgentConfigUserResponse[] | null>(null);

let inflight: Promise<void> | null = null;

export const ensureAgentsLoaded = (token: string): void => {
	if (inflight) return;
	let current: AgentConfigUserResponse[] | null = null;
	const unsub = agentsCache.subscribe((v) => {
		current = v;
	});
	unsub();
	if (current !== null) return;

	inflight = listVisibleAgents(token)
		.then((rows) => {
			agentsCache.set(rows);
		})
		.catch(() => {
			// Leave the cache as null so a later caller can retry.
		})
		.finally(() => {
			inflight = null;
		});
};
