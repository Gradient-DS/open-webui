<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import {
		listVisibleAgents,
		type AgentConfigUserResponse
	} from '$lib/apis/agent-configs';

	const i18n = getContext('i18n');

	export let agentId: string | null | undefined;

	let agent: AgentConfigUserResponse | null = null;
	let resolvedFor: string | null = null;

	$: if (agentId && agentId !== resolvedFor) {
		const requested = agentId;
		listVisibleAgents(localStorage.token)
			.then((rows) => {
				if (requested !== agentId) return;
				agent = rows.find((r) => r.id === requested) ?? null;
				resolvedFor = requested;
			})
			.catch(() => {
				agent = null;
			});
	} else if (!agentId) {
		agent = null;
		resolvedFor = null;
	}

	$: displayName = agent?.name ?? agentId ?? '';
</script>

{#if agentId}
	<span
		class="inline-flex items-center gap-1.5 ml-2 px-2 py-0.5 rounded-full bg-yellow-50 dark:bg-yellow-900/20 text-yellow-800 dark:text-yellow-300 text-[11px] font-medium whitespace-nowrap"
		title={$i18n.t('This chat is bound to an agent') + (agent?.description ? ` — ${agent.description}` : '')}
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
		<span>{displayName}</span>
		{#if agent?.is_beta ?? true}
			<span class="opacity-70">·</span>
			<span>{$i18n.t('Beta')}</span>
		{/if}
	</span>
{/if}
