<script lang="ts">
	import { onMount, onDestroy, getContext, tick } from 'svelte';
	import { goto } from '$app/navigation';

	import { config, pendingAgentId } from '$lib/stores';
	import { isFeatureEnabled } from '$lib/utils/features';
	import {
		listVisibleAgents,
		type AgentConfigUserResponse
	} from '$lib/apis/agent-configs';

	const i18n = getContext('i18n');

	let agents: AgentConfigUserResponse[] = [];
	let loaded = false;

	let scrollContainer: HTMLDivElement | null = null;
	let canScrollLeft = false;
	let canScrollRight = false;

	$: enabled =
		isFeatureEnabled('agent_picker') && Boolean($config?.features?.feature_agent_api_enabled);

	$: isHorizontal = agents.length > 2;

	$: if (isHorizontal && scrollContainer) {
		// Recompute fade visibility whenever the agent list changes.
		tick().then(updateScrollState);
	}

	const updateScrollState = () => {
		if (!scrollContainer) return;
		const { scrollLeft, scrollWidth, clientWidth } = scrollContainer;
		canScrollLeft = scrollLeft > 0;
		canScrollRight = scrollLeft + clientWidth < scrollWidth - 1;
	};

	const handleResize = () => updateScrollState();

	onMount(async () => {
		if (!enabled) {
			loaded = true;
			return;
		}
		try {
			agents = (await listVisibleAgents(localStorage.token)) ?? [];
		} catch {
			agents = [];
		} finally {
			loaded = true;
		}

		// Admin-configured default for the picker on first load. Only kicks
		// in when the user has no localStorage pick yet and the configured
		// slug is actually visible to them (active + has access).
		const defaultSlug = $config?.features?.agent_picker_default_slug;
		if (
			defaultSlug &&
			$pendingAgentId === null &&
			agents.some((a) => a.id === defaultSlug)
		) {
			pendingAgentId.set(defaultSlug);
		}

		window.addEventListener('resize', handleResize);
		await tick();
		updateScrollState();
	});

	onDestroy(() => {
		if (typeof window !== 'undefined') {
			window.removeEventListener('resize', handleResize);
		}
	});

	const startChatWithAgent = async (agentId: string) => {
		// Toggle: clicking the already-selected card clears the pending
		// pick. Otherwise stash the new intent. The chat row only exists
		// once the user actually sends a message — no half-created rows.
		if ($pendingAgentId === agentId) {
			pendingAgentId.set(null);
			return;
		}
		pendingAgentId.set(agentId);
		// If we're already on '/', the empty state will react via the store.
		// If not, navigate there.
		if (window.location.pathname !== '/') {
			await goto('/');
		}
	};
</script>

{#if loaded && enabled && agents.length > 0}
	<div class="mt-4 w-full max-w-3xl mx-auto px-4">
		<!-- Header row: title left, optional "Clear" right. Same height
		     regardless of selection state, so the empty state doesn't jump
		     vertically when the user picks/unpicks an agent. -->
		<div class="mb-2 flex items-center justify-between text-left">
			<span class="text-xs uppercase tracking-wide text-gray-500">
				{$i18n.t('Or use an agent (Beta)')}
			</span>
			<button
				type="button"
				class="text-xs text-yellow-700 dark:text-yellow-400 underline hover:no-underline transition-opacity {$pendingAgentId ? 'opacity-100' : 'opacity-0 pointer-events-none'}"
				aria-hidden={!$pendingAgentId}
				on:click={() => pendingAgentId.set(null)}
			>
				{$i18n.t('Clear')}
			</button>
		</div>

		<div class="relative">
			<div
				bind:this={scrollContainer}
				on:scroll={updateScrollState}
				class={isHorizontal
					? 'flex gap-3 overflow-x-auto scrollbar-hidden snap-x snap-mandatory pb-1'
					: 'grid grid-cols-1 sm:grid-cols-2 gap-3'}
			>
				{#each agents as agent (agent.id)}
					<button
						type="button"
						class="flex flex-col text-left p-3 rounded-2xl border transition {isHorizontal
							? 'shrink-0 w-60 snap-start'
							: ''} {$pendingAgentId === agent.id
							? 'border-yellow-400 bg-yellow-50/40 dark:bg-yellow-900/10'
							: 'border-gray-200 dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-900'}"
						on:click={() => startChatWithAgent(agent.id)}
					>
						<div class="flex items-center gap-2 mb-1">
							{#if agent.profile_image_url}
								<img
									src={agent.profile_image_url}
									alt=""
									class="w-6 h-6 rounded-full object-cover shrink-0"
								/>
							{/if}
							<span class="font-medium truncate">{agent.name}</span>
							{#if agent.is_beta}
								<span
									class="text-[10px] px-1.5 py-0.5 rounded-full bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-300 shrink-0"
								>
									{$i18n.t('Beta')}
								</span>
							{/if}
						</div>
						{#if agent.cta_copy}
							<p class="text-sm text-gray-600 dark:text-gray-400 line-clamp-2">
								{agent.cta_copy}
							</p>
						{:else if agent.description}
							<p class="text-sm text-gray-600 dark:text-gray-400 line-clamp-2">
								{agent.description}
							</p>
						{/if}
					</button>
				{/each}
			</div>

			{#if isHorizontal && canScrollLeft}
				<div
					class="pointer-events-none absolute inset-y-0 left-0 w-10 bg-gradient-to-r from-white dark:from-gray-900 to-transparent"
				></div>
			{/if}
			{#if isHorizontal && canScrollRight}
				<div
					class="pointer-events-none absolute inset-y-0 right-0 w-10 bg-gradient-to-l from-white dark:from-gray-900 to-transparent"
				></div>
			{/if}
		</div>
	</div>
{/if}
