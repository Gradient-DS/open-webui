<script lang="ts">
	import { reduceSubAgents } from './reduceSubAgents';
	import SubAgentCard from './SubAgentCard.svelte';

	import type { SubAgentEvent } from '$lib/types/subagent';

	// Flat list of `event: subagent` SSE payloads accumulated by Chat.svelte
	// onto `message.subagents`. Non-bezwaar agents leave this empty / undefined,
	// in which case the reducer returns `[]` and the component renders nothing.
	export let events: SubAgentEvent[] = [];

	$: groups = reduceSubAgents(events ?? []);
</script>

{#if groups.length > 0}
	<div class="my-2 flex flex-col gap-2">
		{#each groups as group (group.parallel_group_id)}
			{#if group.cards.length > 1}
				<div class="flex flex-col sm:flex-row gap-2">
					{#each group.cards as card (card.agent_id)}
						<div class="flex-1 min-w-0">
							<SubAgentCard {card} />
						</div>
					{/each}
				</div>
			{:else}
				<SubAgentCard card={group.cards[0]} />
			{/if}
		{/each}
	</div>
{/if}
