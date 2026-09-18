<script context="module" lang="ts">
	export { sourcesSheetClass } from './sourcesSheet';
</script>

<script lang="ts">
	import { getContext } from 'svelte';
	import type { i18n as I18n } from 'i18next';
	import type { Readable } from 'svelte/store';
	const i18n = getContext<Readable<I18n>>('i18n');
	export let expanded = false;
</script>

<button
	class="shrink-0 w-full flex justify-center py-3"
	aria-label={$i18n.t('Toggle citation panel height')}
	aria-expanded={expanded}
	on:click={() => (expanded = !expanded)}
>
	<span class="w-10 h-1 rounded-full bg-gray-300 dark:bg-gray-600"></span>
</button>
