<script context="module" lang="ts">
	import { writable } from 'svelte/store';
	import { listVisibleAgents, type AgentConfigUserResponse } from '$lib/apis/agent-configs';

	// Module-scoped cache shared across every AgentBadge instance on the
	// page. Pre-loaded once per session — looking up the agent for a
	// given slug is then synchronous, which is what kills the
	// fetch-during-switch flicker (raw slug briefly visible while the
	// network round-trip resolved).
	//
	// ``null`` = not yet loaded; an empty array = loaded but no rows.
	const agentsCache = writable<AgentConfigUserResponse[] | null>(null);
	let inflight: Promise<void> | null = null;

	const ensureAgentsLoaded = (token: string): void => {
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
				// Leave the cache as null so a later instance can retry.
			})
			.finally(() => {
				inflight = null;
			});
	};
</script>

<script lang="ts">
	import { onMount, getContext } from 'svelte';

	import Dropdown from '$lib/components/common/Dropdown.svelte';

	const i18n = getContext('i18n');

	export let agentId: string | null | undefined;

	let show = false;

	onMount(() => {
		ensureAgentsLoaded(localStorage.token);
	});

	// Synchronous lookup against the shared cache. As soon as ``agentId``
	// changes, ``agent`` updates in the same render pass — the pill swaps
	// from one display name to the next without an intermediate raw-slug
	// frame.
	$: agent = $agentsCache?.find((r) => r.id === agentId) ?? null;

	// Cache loaded but the requested slug isn't in it (admin removed it
	// from AGENT_API_AGENTS, or the row hasn't been fetched yet) — fall
	// back to the slug, dimmed, so the user still sees something.
	$: cacheLoaded = $agentsCache !== null;
	$: showOrphanFallback = cacheLoaded && !!agentId && !agent;
</script>

{#if agentId && (agent || showOrphanFallback)}
	<Dropdown
		bind:show
		side="bottom"
		align="start"
		sideOffset={6}
		contentClass="w-72 rounded-xl border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 shadow-lg p-3 text-xs text-gray-700 dark:text-gray-200"
	>
		<button
			type="button"
			aria-haspopup="dialog"
			aria-expanded={show}
			class="inline-flex items-center gap-1.5 ml-2 px-2 py-0.5 rounded-full bg-yellow-50 dark:bg-yellow-900/20 text-yellow-800 dark:text-yellow-300 text-[11px] font-medium whitespace-nowrap hover:bg-yellow-100 dark:hover:bg-yellow-900/30 transition focus:outline-hidden focus-visible:ring-2 focus-visible:ring-yellow-400/60"
		>
			{#if agent?.profile_image_url}
				<img src={agent.profile_image_url} alt="" class="w-3.5 h-3.5 rounded-full object-cover" />
			{:else}
				<svg
					xmlns="http://www.w3.org/2000/svg"
					viewBox="0 0 24 24"
					fill="currentColor"
					class="w-3 h-3"
				>
					<path
						fill-rule="evenodd"
						d="M12 2.25a.75.75 0 0 1 .75.75v.518A6 6 0 0 1 18 9v3.75a3 3 0 0 1-3 3H9a3 3 0 0 1-3-3V9a6 6 0 0 1 5.25-5.482V3a.75.75 0 0 1 .75-.75ZM9.75 9.75a.75.75 0 0 0-1.5 0v1.5a.75.75 0 0 0 1.5 0v-1.5Zm6 0a.75.75 0 0 0-1.5 0v1.5a.75.75 0 0 0 1.5 0v-1.5ZM7.5 17.25a.75.75 0 0 1 .75.75c0 .414.336.75.75.75h6a.75.75 0 0 0 .75-.75.75.75 0 0 1 1.5 0 2.25 2.25 0 0 1-2.25 2.25H9a2.25 2.25 0 0 1-2.25-2.25.75.75 0 0 1 .75-.75Z"
						clip-rule="evenodd"
					/>
				</svg>
			{/if}
			{#if agent}
				<span>{agent.name}</span>
				{#if agent.is_beta ?? true}
					<span class="opacity-70">·</span>
					<span>{$i18n.t('Beta')}</span>
				{/if}
			{:else}
				<!-- Cache loaded but slug missing — orphaned binding. Show
				     the slug muted so it's clear this chat references an
				     agent the admin no longer exposes. -->
				<span class="opacity-70">{agentId}</span>
			{/if}
		</button>

		<div slot="content">
			<div class="flex items-center gap-2 mb-2">
				{#if agent?.profile_image_url}
					<img
						src={agent.profile_image_url}
						alt=""
						class="w-5 h-5 rounded-full object-cover shrink-0"
					/>
				{/if}
				<span class="font-medium text-sm truncate">
					{agent?.name ?? agentId}
				</span>
				{#if agent?.is_beta}
					<span
						class="text-[10px] px-1.5 py-0.5 rounded-full bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-300 shrink-0"
					>
						{$i18n.t('Beta')}
					</span>
				{/if}
			</div>
			{#if agent?.description}
				<p class="text-xs text-gray-600 dark:text-gray-400 mb-2 whitespace-pre-wrap">
					{agent.description}
				</p>
			{/if}
			<div class="text-[11px] text-gray-500 dark:text-gray-500 border-t border-gray-100 dark:border-gray-800 pt-2 mt-1">
				{$i18n.t('Start a new chat to switch agents.')}
			</div>
		</div>
	</Dropdown>
{/if}
