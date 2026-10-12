<script lang="ts">
	// [Gradient] The current state of a composer tool as text; its row cycles the state.
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { getContext } from 'svelte';

	import type { ToolState } from '$lib/utils/toolState';

	const i18n = getContext<Writable<i18nType>>('i18n');

	export let state: ToolState;

	const classes: Record<ToolState, string> = {
		off: 'text-gray-400 dark:text-gray-500 border-gray-200 dark:border-gray-700',
		auto: 'text-gray-700 dark:text-gray-200 bg-gray-100 dark:bg-gray-800 border-gray-200 dark:border-gray-700',
		required:
			'text-sky-600 dark:text-sky-300 bg-sky-50 dark:bg-sky-400/10 border-sky-200 dark:border-sky-500/30'
	};
</script>

<span
	class="shrink-0 min-w-11 px-1.5 h-5 inline-flex items-center justify-center rounded-md border text-[0.6875rem] font-medium {classes[
		state
	]}"
>
	{#if state === 'auto'}
		{$i18n.t('Auto')}
	{:else if state === 'required'}
		{$i18n.t('Always')}
	{:else}
		{$i18n.t('Off')}
	{/if}
</span>
