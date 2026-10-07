<script lang="ts">
	// [Claude] Lines above an agent answer naming the chat items the turn ran without; the backend writes them in
	// the user's language.
	import InfoCircle from '$lib/components/icons/InfoCircle.svelte';

	export let notices: string[] = [];
</script>

{#if notices?.length}
	<div
		class="turn-notices flex flex-col gap-0.5 mb-1.5 text-xs text-gray-500 dark:text-gray-400"
		role="note"
	>
		{#each notices as notice}
			<div class="flex items-start gap-1.5">
				<InfoCircle className="size-3.5 shrink-0 mt-px" />
				<span>{notice}</span>
			</div>
		{/each}
	</div>
{/if}
