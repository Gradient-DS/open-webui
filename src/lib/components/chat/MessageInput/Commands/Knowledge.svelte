<script lang="ts">
	import { toast } from 'svelte-sonner';
	import dayjs from 'dayjs';
	import relativeTime from 'dayjs/plugin/relativeTime';
	dayjs.extend(relativeTime);

	import { tick, getContext, onMount, onDestroy } from 'svelte';

	import { folders } from '$lib/stores';
	import { getFolders } from '$lib/apis/folders';
	import { searchKnowledgeBases } from '$lib/apis/knowledge';
	import { removeLastWordFromString, isValidHttpUrl, isYoutubeUrl, decodeString } from '$lib/utils';

	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Database from '$lib/components/icons/Database.svelte';
	import OneDrive from '$lib/components/icons/OneDrive.svelte';
	import GoogleDrive from '$lib/components/icons/GoogleDrive.svelte';
	import Confluence from '$lib/components/icons/Confluence.svelte';
	import GlobeAlt from '$lib/components/icons/GlobeAlt.svelte';
	import Youtube from '$lib/components/icons/Youtube.svelte';
	import Folder from '$lib/components/icons/Folder.svelte';

	const i18n = getContext('i18n');

	export let query = '';
	export let onSelect = (e) => {};

	let selectedIdx = 0;
	let items = [];
	let searchDebounceTimer: ReturnType<typeof setTimeout>;

	export let filteredItems = [];
	$: filteredItems = [
		...(query.startsWith('http')
			? isYoutubeUrl(query)
				? [{ type: 'youtube', name: query, description: query }]
				: [
						{
							type: 'web',
							name: query,
							description: query
						}
					]
			: []),
		...items
	];

	$: if (query) {
		selectedIdx = 0;
	}

	export const selectUp = () => {
		selectedIdx = Math.max(0, selectedIdx - 1);
	};

	export const selectDown = () => {
		selectedIdx = Math.min(selectedIdx + 1, filteredItems.length - 1);
	};

	export const select = async () => {
		// find item with data-selected=true
		const item = document.querySelector(`[data-selected="true"]`);
		if (item) {
			// click the item
			item.click();
		}
	};

	let folderItems = [];
	let knowledgeItems = [];

	$: items = [...folderItems, ...knowledgeItems];

	$: if (query !== undefined) {
		clearTimeout(searchDebounceTimer);
		searchDebounceTimer = setTimeout(() => {
			getItems();
		}, 200);
	}

	onDestroy(() => {
		clearTimeout(searchDebounceTimer);
	});

	const getItems = () => {
		getFolderItems();
		getKnowledgeItems();
	};

	const getFolderItems = async () => {
		folderItems = $folders
			.map((folder) => ({
				...folder,
				type: 'folder',
				description: $i18n.t('Folder'),
				title: folder.name
			}))
			.filter((folder) => folder.name.toLowerCase().includes(query.toLowerCase()));
	};

	const getKnowledgeItems = async () => {
		const res = await searchKnowledgeBases(localStorage.token, query).catch(() => {
			return null;
		});

		if (res) {
			knowledgeItems = res.items.map((item) => {
				return {
					...item,
					knowledge_type: item.type,
					type: 'collection'
				};
			});
		}
	};

	onMount(async () => {
		if ($folders === null) {
			await folders.set(await getFolders(localStorage.token));
		}

		await tick();
	});
</script>

{#if filteredItems.length > 0 || query.startsWith('http')}
	{#each filteredItems as item, idx}
		{#if idx === 0 || item?.type !== items[idx - 1]?.type}
			<div class="px-2 text-xs text-gray-500 py-1">
				{#if item?.type === 'folder'}
					{$i18n.t('Folders')}
				{:else if item?.type === 'collection'}
					{$i18n.t('Collections')}
				{/if}
			</div>
		{/if}

		{#if !['youtube', 'web'].includes(item.type)}
			<button
				class=" px-2 py-1 rounded-xl w-full text-left flex justify-between items-center {idx ===
				selectedIdx
					? ' bg-gray-50 dark:bg-gray-800 dark:text-gray-100 selected-command-option-button'
					: ''}"
				type="button"
				on:click={() => {
					console.log(item);
					onSelect({
						type: 'knowledge',
						data: item
					});
				}}
				on:mousemove={() => {
					selectedIdx = idx;
				}}
				data-selected={idx === selectedIdx}
			>
				<div class="  text-black dark:text-gray-100 flex items-center gap-1">
					<Tooltip
						content={item?.legacy
							? $i18n.t('Legacy')
							: item?.type === 'collection'
								? $i18n.t('Collection')
								: ''}
						placement="top"
					>
						{#if item?.type === 'collection'}
							{#if item.knowledge_type === 'onedrive'}
								<OneDrive className="size-4" />
							{:else if item.knowledge_type === 'google_drive'}
								<GoogleDrive className="size-4" />
							{:else if item.knowledge_type === 'confluence'}
								<Confluence className="size-4" />
							{:else}
								<Database className="size-4" />
							{/if}
						{:else if item?.type === 'folder'}
							<Folder className="size-4" />
						{/if}
					</Tooltip>

					<Tooltip content={`${decodeString(item?.name)}`} placement="top-start">
						<div class="line-clamp-1 flex-1">
							{decodeString(item?.name)}
						</div>
					</Tooltip>
				</div>
			</button>
		{/if}
	{/each}

	{#if isYoutubeUrl(query)}
		<button
			class="px-2 py-1 rounded-xl w-full text-left bg-gray-50 dark:bg-gray-800 dark:text-gray-100 selected-command-option-button"
			type="button"
			data-selected={selectedIdx === filteredItems.findIndex((i) => i.type === 'youtube')}
			on:click={() => {
				if (isValidHttpUrl(query)) {
					onSelect({
						type: 'web',
						data: query
					});
				} else {
					toast.error(
						$i18n.t('Oops! Looks like the URL is invalid. Please double-check and try again.')
					);
				}
			}}
		>
			<div class="  text-black dark:text-gray-100 line-clamp-1 flex items-center gap-1">
				<Tooltip content={$i18n.t('YouTube')} placement="top">
					<Youtube className="size-4" />
				</Tooltip>

				<div class="truncate flex-1">
					{query}
				</div>
			</div>
		</button>
	{:else if query.startsWith('http')}
		<button
			class="px-2 py-1 rounded-xl w-full text-left bg-gray-50 dark:bg-gray-800 dark:text-gray-100 selected-command-option-button"
			type="button"
			data-selected={selectedIdx === filteredItems.findIndex((i) => i.type === 'web')}
			on:click={() => {
				if (isValidHttpUrl(query)) {
					onSelect({
						type: 'web',
						data: query
					});
				} else {
					toast.error(
						$i18n.t('Oops! Looks like the URL is invalid. Please double-check and try again.')
					);
				}
			}}
		>
			<div class="  text-black dark:text-gray-100 line-clamp-1 flex items-center gap-1">
				<Tooltip content={$i18n.t('Web')} placement="top">
					<GlobeAlt className="size-4" />
				</Tooltip>

				<div class="truncate flex-1">
					{query}
				</div>
			</div>
		</button>
	{/if}
{/if}
