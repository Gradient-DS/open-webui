<script lang="ts">
	import { onMount, getContext } from 'svelte';
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

	$: enabled =
		isFeatureEnabled('agent_picker') && Boolean($config?.features?.feature_agent_api_enabled);

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
	<div class="mt-6 w-full max-w-3xl mx-auto px-4">
		<!-- Header row: title left, optional "Clear" right. Same height
		     regardless of selection state, so the empty state doesn't jump
		     vertically when the user picks/unpicks an agent. -->
		<div class="mb-3 flex items-center justify-between text-left">
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
		<div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
			{#each agents as agent (agent.id)}
				<button
					type="button"
					class="flex flex-col text-left p-4 rounded-2xl border transition {$pendingAgentId === agent.id ? 'border-yellow-400 bg-yellow-50/40 dark:bg-yellow-900/10' : 'border-gray-200 dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-900'}"
					on:click={() => startChatWithAgent(agent.id)}
				>
					<div class="flex items-center gap-2 mb-1">
						{#if agent.profile_image_url}
							<img
								src={agent.profile_image_url}
								alt=""
								class="w-6 h-6 rounded-full object-cover"
							/>
						{/if}
						<span class="font-medium">{agent.name}</span>
						{#if agent.is_beta}
							<span
								class="text-[10px] px-1.5 py-0.5 rounded-full bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-300"
							>
								{$i18n.t('Beta')}
							</span>
						{/if}
					</div>
					{#if agent.cta_copy}
						<p class="text-sm text-gray-600 dark:text-gray-400">{agent.cta_copy}</p>
					{:else if agent.description}
						<p class="text-sm text-gray-600 dark:text-gray-400">{agent.description}</p>
					{/if}
				</button>
			{/each}
		</div>
	</div>
{/if}
