<script lang="ts">
	import { getContext } from 'svelte';
	import GarbageBin from '$lib/components/icons/GarbageBin.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';

	const i18n = getContext('i18n');

	// Always-present selection header. Reserving its height means selecting the
	// first row never shifts the list (no UI jump).
	export let count: number = 0;
	export let allSelected: boolean = false;
	export let indeterminate: boolean = false;
	export let onToggleSelectAll: () => void = () => {};
	export let onDelete: () => void = () => {};
	export let onClear: () => void = () => {};
</script>

<div class="flex items-center gap-2 px-3 py-1.5 rounded-lg {count > 0
		? 'bg-gray-50 dark:bg-gray-800/50'
		: ''}">
	<Tooltip content={allSelected ? $i18n.t('Deselect') : $i18n.t('Select All')}>
		<button
			type="button"
			class="flex items-center"
			on:click={onToggleSelectAll}
			aria-label={allSelected ? $i18n.t('Deselect') : $i18n.t('Select All')}
		>
			<div
				class="size-4 shrink-0 rounded border flex items-center justify-center transition-colors {allSelected ||
				indeterminate
					? 'bg-blue-500 dark:bg-blue-600 border-blue-500 dark:border-blue-600 text-white'
					: 'border-gray-300 dark:border-gray-600'}"
			>
				{#if allSelected}
					<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" class="size-3">
						<path
							fill-rule="evenodd"
							d="M16.704 4.153a.75.75 0 0 1 .143 1.052l-8 10.5a.75.75 0 0 1-1.127.075l-4.5-4.5a.75.75 0 0 1 1.06-1.06l3.894 3.893 7.48-9.817a.75.75 0 0 1 1.05-.143Z"
							clip-rule="evenodd"
						/>
					</svg>
				{:else if indeterminate}
					<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" class="size-3">
						<path
							fill-rule="evenodd"
							d="M4 10a.75.75 0 0 1 .75-.75h10.5a.75.75 0 0 1 0 1.5H4.75A.75.75 0 0 1 4 10Z"
							clip-rule="evenodd"
						/>
					</svg>
				{/if}
			</div>
		</button>
	</Tooltip>

	{#if count > 0}
		<span class="text-xs font-medium text-gray-600 dark:text-gray-400 flex-1 truncate">
			{$i18n.t('{{count}} selected', { count })}
		</span>

		<Tooltip content={$i18n.t('Delete')}>
			<button
				class="p-1 rounded transition text-gray-400 dark:text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800 hover:text-gray-600 dark:hover:text-gray-400"
				on:click={onDelete}
				aria-label={$i18n.t('Delete')}
			>
				<GarbageBin className="size-3.5" />
			</button>
		</Tooltip>

		<Tooltip content={$i18n.t('Deselect')}>
			<button
				class="p-1 rounded transition text-gray-400 dark:text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800 hover:text-gray-600 dark:hover:text-gray-400"
				on:click={onClear}
				aria-label={$i18n.t('Deselect')}
			>
				<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" class="size-3.5">
					<path
						d="M6.28 5.22a.75.75 0 0 0-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 1 0 1.06 1.06L10 11.06l3.72 3.72a.75.75 0 1 0 1.06-1.06L11.06 10l3.72-3.72a.75.75 0 0 0-1.06-1.06L10 8.94 6.28 5.22Z"
					/>
				</svg>
			</button>
		</Tooltip>
	{:else}
		<span class="text-xs text-gray-400 dark:text-gray-500 flex-1 truncate select-none">
			{$i18n.t('Select All')}
		</span>
	{/if}
</div>
