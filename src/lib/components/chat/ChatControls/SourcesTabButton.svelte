<script lang="ts">
	import { getContext } from 'svelte';
	import type { i18n as I18n } from 'i18next';
	import type { Readable } from 'svelte/store';
	const i18n = getContext<Readable<I18n>>('i18n');
	export let activeTab: string;
	export let onSelect: () => void;
</script>

<button
	class="px-2.5 py-1 text-sm rounded-lg transition whitespace-nowrap {activeTab === 'sources'
		? 'bg-gray-100/40 dark:bg-gray-800/25 font-normal text-gray-700 dark:text-gray-200'
		: 'text-gray-500 dark:text-gray-400 hover:bg-gray-100/30 dark:hover:bg-gray-800/20 hover:text-gray-600 dark:hover:text-gray-300'}"
	on:click={onSelect}
>
	{$i18n.t('Sources')}
</button>
