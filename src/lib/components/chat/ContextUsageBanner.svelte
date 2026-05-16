<script lang="ts">
	// [Gradient] Renders a banner above the chat input when the agent
	// service reports that the conversation has filled a large fraction
	// of the LLM's effective context window. Driven by the
	// `context_usage` Socket.IO event emitted by the agents backend
	// once per turn. The user should start a new chat for unrelated
	// topics to keep model performance high.
	import { getContext } from 'svelte';
	import { fade } from 'svelte/transition';

	const i18n: any = getContext('i18n');

	export let usage: {
		tokens_used: number;
		tokens_budget: number;
		fraction: number;
	} | null = null;

	$: fraction = usage ? Math.min(1, Math.max(0, usage.fraction)) : 0;
	$: percentage = Math.round(fraction * 100);
	// Warning aligns with the agent's compaction threshold (0.75): when this
	// fires, the user's next message will trigger compaction at the start
	// of the next turn. Subtle and neutral nudge toward a new chat before
	// that point.
	$: severity = !usage
		? 'hidden'
		: fraction >= 0.75
			? 'warning'
			: fraction >= 0.6
				? 'neutral'
				: fraction >= 0.45
					? 'subtle'
					: 'hidden';

	$: message =
		severity === 'warning'
			? $i18n.t(
					'{{percentage}}% of the context window used — older context will be summarized on your next message. For the best results on a new topic, start a new chat.',
					{ percentage }
				)
			: severity === 'neutral'
				? $i18n.t(
						'{{percentage}}% of the context window used. Start a new chat for unrelated topics.',
						{ percentage }
					)
				: $i18n.t('{{percentage}}% of the context window used.', { percentage });
</script>

{#if severity !== 'hidden'}
	<div
		class="mx-auto w-full max-w-5xl px-2.5 sm:px-4 pb-2 text-xs"
		transition:fade={{ duration: 150 }}
	>
		<div
			class="flex items-center gap-2 rounded-lg px-3 py-2 border {severity === 'warning'
				? 'bg-yellow-50 dark:bg-yellow-500/10 border-yellow-300 dark:border-yellow-500/40 text-yellow-900 dark:text-yellow-200'
				: severity === 'neutral'
					? 'bg-gray-50 dark:bg-gray-850 border-gray-200 dark:border-gray-700 text-gray-700 dark:text-gray-300'
					: 'bg-transparent border-gray-100 dark:border-gray-800 text-gray-500 dark:text-gray-400'}"
			role="status"
			aria-live="polite"
		>
			<svg
				xmlns="http://www.w3.org/2000/svg"
				viewBox="0 0 24 24"
				fill="none"
				stroke="currentColor"
				stroke-width="2"
				stroke-linecap="round"
				stroke-linejoin="round"
				class="size-4 shrink-0"
				aria-hidden="true"
			>
				<circle cx="12" cy="12" r="10"></circle>
				<line x1="12" y1="8" x2="12" y2="12"></line>
				<line x1="12" y1="16" x2="12.01" y2="16"></line>
			</svg>
			<span class="leading-snug">{message}</span>
		</div>
	</div>
{/if}
