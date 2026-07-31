<script lang="ts">
	// [Gradient] Agent selector rendered in the navbar's model-selector slot.
	//
	// When the agent picker feature owns chat routing, this replaces the
	// ModelSelector (which would otherwise show the task-model id — noise on
	// single-LLM deployments). Deliberately a separate fork-owned component:
	// ModelSelector.svelte stays at zero upstream divergence, and agent ids
	// never enter the selectedModels value space (which three reconcilers
	// blank against $models and utils/agent.py forwards as llm_model).
	//
	// Same binding semantics as AgentBadge/AgentCards: picks write the shared
	// pendingAgentId store; Chat.svelte threads it into chat.meta.agent_id on
	// first send. Editable only on the empty state — switching agents
	// mid-chat is not a thing (backend routes on the chat row).
	import { onMount, getContext } from 'svelte';
	import type { Writable } from 'svelte/store';

	import { config, pendingAgentId } from '$lib/stores';
	import { agentsCache, ensureAgentsLoaded } from '$lib/stores/agent-cache';
	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import Check from '$lib/components/icons/Check.svelte';

	const i18n: Writable<any> = getContext('i18n');

	// Bound agent id: chat.meta.agent_id on saved chats, $pendingAgentId on
	// the empty state (threaded by Navbar, same as AgentBadge got).
	export let agentId: string | null | undefined;
	export let editable = false;

	let show = false;

	onMount(() => {
		ensureAgentsLoaded(localStorage.token);
	});

	$: agents = $agentsCache ?? [];
	$: cacheLoaded = $agentsCache !== null;
	$: agent = agents.find((r) => r.id === agentId) ?? null;

	// Mirror AgentCards: apply the admin-configured default on first load so
	// the selector — like the model picker — always shows a selection when
	// the deployment configured one. Respects an existing sticky pick.
	$: if (editable && cacheLoaded && $pendingAgentId === null) {
		const defaultSlug = $config?.features?.agent_picker_default_slug;
		if (defaultSlug && agents.some((a) => a.id === defaultSlug)) {
			pendingAgentId.set(defaultSlug);
		}
	}

	const selectAgent = (id: string): void => {
		pendingAgentId.set(id);
		show = false;
	};
</script>

{#if cacheLoaded && (agents.length > 0 || agentId)}
	{#if editable}
		<Dropdown
			bind:show
			side="bottom"
			align="start"
			sideOffset={6}
			contentClass="w-80 rounded-xl border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 shadow-lg p-2 text-gray-700 dark:text-gray-200"
		>
			<button
				type="button"
				aria-haspopup="listbox"
				aria-expanded={show}
				aria-label={agent
					? $i18n.t('Selected model: {{modelName}}', { modelName: agent.name })
					: $i18n.t('Choose agent')}
				class="relative w-full outline-hidden focus:outline-hidden"
			>
				<div class="flex w-full text-left px-0.5 bg-transparent truncate text-lg justify-between">
					{#if agent}
						{agent.name}
					{:else}
						{$i18n.t('Choose agent')}
					{/if}
					<ChevronDown className=" self-center ml-2 size-3" strokeWidth="2.5" />
				</div>
			</button>

			<div slot="content">
				<div class="flex flex-col gap-0.5 max-h-80 overflow-y-auto" role="listbox">
					{#each agents as a (a.id)}
						<button
							type="button"
							role="option"
							aria-selected={agentId === a.id}
							class="flex flex-col text-left px-3 py-2 rounded-lg transition {agentId === a.id
								? 'bg-gray-100 dark:bg-gray-800'
								: 'hover:bg-gray-50 dark:hover:bg-gray-850'}"
							on:click={() => selectAgent(a.id)}
						>
							<div class="flex items-center gap-2 w-full">
								{#if a.profile_image_url}
									<img
										src={a.profile_image_url}
										alt=""
										class="w-5 h-5 rounded-full object-cover shrink-0"
									/>
								{/if}
								<span class="font-medium text-sm truncate">{a.name}</span>
								{#if agentId === a.id}
									<span class="ml-auto shrink-0">
										<Check className="size-3.5" />
									</span>
								{/if}
							</div>
							{#if a.description}
								<p class="text-xs text-gray-500 dark:text-gray-400 line-clamp-2 mt-0.5">
									{a.description}
								</p>
							{/if}
						</button>
					{/each}
				</div>
			</div>
		</Dropdown>
	{:else}
		<!-- Saved chat: routing is fixed by the chat row — plain label, no
		     affordance to switch (matches the disabled model selector). Falls
		     back to the raw slug when the agent is no longer exposed. -->
		<div class="flex text-left px-0.5 text-lg truncate max-w-full">
			{#if agent}
				{agent.name}
			{:else}
				<span class="opacity-70">{agentId}</span>
			{/if}
		</div>
	{/if}
{/if}
