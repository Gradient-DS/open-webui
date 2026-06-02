<script lang="ts">
	import { onMount, tick, getContext } from 'svelte';

	import { decodeString } from '$lib/utils';
	import { knowledge } from '$lib/stores';

	import { getKnowledgeBases } from '$lib/apis/knowledge';

	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Database from '$lib/components/icons/Database.svelte';
	import OneDrive from '$lib/components/icons/OneDrive.svelte';
	import GoogleDrive from '$lib/components/icons/GoogleDrive.svelte';
	import Confluence from '$lib/components/icons/Confluence.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Loader from '$lib/components/common/Loader.svelte';

	const i18n = getContext('i18n');

	export let onSelect = (e) => {};

	let loaded = false;
	let selectedIdx = 0;

	let page = 1;
	let items = null;
	let total = null;

	let itemsLoading = false;
	let allItemsLoaded = false;

	$: if (loaded) {
		init();
	}

	const init = async () => {
		reset();
		await tick();
		await getItemsPage();
	};

	const reset = () => {
		page = 1;
		items = null;
		total = null;
		allItemsLoaded = false;
		itemsLoading = false;
	};

	const loadMoreItems = async () => {
		if (allItemsLoaded) return;
		page += 1;
		await getItemsPage();
	};

	const getItemsPage = async () => {
		itemsLoading = true;
		const res = await getKnowledgeBases(localStorage.token, page).catch(() => {
			return null;
		});

		if (res) {
			console.log(res);
			total = res.total;
			const pageItems = res.items;

			if ((pageItems ?? []).length === 0) {
				allItemsLoaded = true;
			} else {
				allItemsLoaded = false;
			}

			if (items) {
				const existingIds = new Set(items.map((item) => item.id));
				const newItems = pageItems.filter((item) => !existingIds.has(item.id));
				items = [...items, ...newItems];
			} else {
				items = pageItems;
			}
		}

		itemsLoading = false;
		return res;
	};

	onMount(async () => {
		await tick();
		loaded = true;
	});
</script>

{#if loaded && items !== null}
	<div class="flex flex-col gap-0.5">
		{#if items.length === 0}
			<div class="py-4 text-center text-sm text-gray-500 dark:text-gray-400">
				{$i18n.t('No knowledge bases found.')}
			</div>
		{:else}
			{#each items as item, idx (item.id)}
				<div
					class=" px-2.5 py-1 rounded-xl w-full text-left flex justify-between items-center text-sm {idx ===
					selectedIdx
						? ' bg-gray-50 dark:bg-gray-800 dark:text-gray-100 selected-command-option-button'
						: ''}"
				>
					<button
						class="w-full flex-1"
						type="button"
						on:click={() => {
							onSelect({
								...item,
								knowledge_type: item.type,
								type: 'collection'
							});
						}}
						on:mousemove={() => {
							selectedIdx = idx;
						}}
						on:mouseleave={() => {
							if (idx === 0) {
								selectedIdx = -1;
							}
						}}
						data-selected={idx === selectedIdx}
					>
						<div class="w-full text-left text-black dark:text-gray-100 flex items-center gap-1">
							<Tooltip content={$i18n.t('Collection')} placement="top">
								{#if item.type === 'onedrive'}
									<OneDrive className="size-4" />
								{:else if item.type === 'google_drive'}
									<GoogleDrive className="size-4" />
								{:else if item.type === 'confluence'}
									<Confluence className="size-4" />
								{:else}
									<Database className="size-4" />
								{/if}
							</Tooltip>

							<Tooltip
								content={item.description || decodeString(item?.name)}
								placement="top-start"
								className="flex flex-1 min-w-0"
							>
								<div class="line-clamp-1 flex-1 text-sm">
									{decodeString(item?.name)}
								</div>
							</Tooltip>
						</div>
					</button>
				</div>
			{/each}

			{#if !allItemsLoaded}
				<Loader
					on:visible={(e) => {
						if (!itemsLoading) {
							loadMoreItems();
						}
					}}
				>
					<div class="w-full flex justify-center py-4 text-xs animate-pulse items-center gap-2">
						<Spinner className=" size-4" />
						<div class=" ">{$i18n.t('Loading...')}</div>
					</div>
				</Loader>
			{/if}
		{/if}
	</div>
{:else}
	<div class="py-4.5">
		<Spinner />
	</div>
{/if}
