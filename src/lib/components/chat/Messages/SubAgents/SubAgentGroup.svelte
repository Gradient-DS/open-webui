<script lang="ts">
	import SubAgentCard from './SubAgentCard.svelte';

	import type { SubAgentGroupVM } from '$lib/types/subagent';

	// Pre-reduced group view-model — the parent (ResponseMessage) runs the
	// reducer once per animation frame and passes the result down. This
	// component used to accept raw ``SubAgentEvent[]`` and re-run the reducer
	// itself; that doubled the per-token cost during streaming for every
	// rendered group. Layout-only now.
	export let group: SubAgentGroupVM;
</script>

{#if group.cards.length > 0}
	<div class="my-2 flex flex-col gap-2">
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
	</div>
{/if}
