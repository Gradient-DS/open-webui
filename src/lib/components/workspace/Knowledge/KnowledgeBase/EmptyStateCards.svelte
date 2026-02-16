<script lang="ts">
	import { getContext } from 'svelte';
	const i18n = getContext('i18n');

	import ArrowUpCircle from '$lib/components/icons/ArrowUpCircle.svelte';
	import FolderOpen from '$lib/components/icons/FolderOpen.svelte';
	import GlobeAlt from '$lib/components/icons/GlobeAlt.svelte';
	import BarsArrowUp from '$lib/components/icons/BarsArrowUp.svelte';
	import OneDrive from '$lib/components/icons/OneDrive.svelte';

	export let knowledgeType: string = 'local';
	export let onAction: (type: string) => void = () => {};

	type UploadOption = {
		type: string;
		label: string;
		icon: any;
		description: string;
	};

	$: options = getOptions(knowledgeType);

	function getOptions(type: string): UploadOption[] {
		if (type === 'onedrive') {
			return [
				{
					type: 'onedrive',
					label: 'Sync from OneDrive',
					icon: OneDrive,
					description: 'Select files and folders to sync'
				}
			];
		}
		return [
			{
				type: 'files',
				label: 'Upload files',
				icon: ArrowUpCircle,
				description: 'Upload documents from your device'
			},
			{
				type: 'directory',
				label: 'Upload directory',
				icon: FolderOpen,
				description: 'Upload an entire folder'
			},
			{
				type: 'web',
				label: 'Add webpage',
				icon: GlobeAlt,
				description: 'Import content from a URL'
			},
			{
				type: 'text',
				label: 'Add text content',
				icon: BarsArrowUp,
				description: 'Paste or write text directly'
			}
		];
	}

	$: gridCols =
		options.length <= 1
			? 'grid-cols-1 max-w-sm mx-auto'
			: options.length === 2 || options.length === 4
				? 'grid-cols-2'
				: options.length === 3
					? 'grid-cols-3'
					: 'grid-cols-3';
</script>

<div class="flex flex-col items-center justify-center w-full h-full py-8 px-4">
	<div class="text-center mb-6">
		<div class="text-sm font-medium text-gray-500 dark:text-gray-400">
			{$i18n.t('Get started by adding content')}
		</div>
	</div>

	<div class="grid {gridCols} gap-4 w-full max-w-2xl">
		{#each options as option}
			<button
				class="flex flex-col items-center justify-center gap-3 p-8
					border border-dashed border-gray-300 dark:border-gray-600
					rounded-2xl cursor-pointer
					hover:bg-gray-50 dark:hover:bg-gray-850
					hover:border-gray-400 dark:hover:border-gray-500
					transition-all duration-150"
				on:click={() => onAction(option.type)}
			>
				<div class="text-gray-400 dark:text-gray-500">
					<svelte:component this={option.icon} className="size-8" strokeWidth="1.5" />
				</div>
				<div class="text-sm font-medium text-gray-700 dark:text-gray-300">
					{$i18n.t(option.label)}
				</div>
				<div class="text-xs text-gray-400 dark:text-gray-500">
					{$i18n.t(option.description)}
				</div>
			</button>
		{/each}
	</div>
</div>
