<script lang="ts">
	import dayjs from 'dayjs';
	import relativeTime from 'dayjs/plugin/relativeTime';
	dayjs.extend(relativeTime);

	import { toast } from 'svelte-sonner';
	import { onMount, getContext, tick, onDestroy } from 'svelte';
	const i18n = getContext('i18n');

	import { WEBUI_NAME, knowledge, user, config, socket } from '$lib/stores';
	import {
		deleteKnowledgeById,
		searchKnowledgeBases,
		exportKnowledgeById
	} from '$lib/apis/knowledge';

	import { goto } from '$app/navigation';
	import { capitalizeFirstLetter } from '$lib/utils';

	import { DropdownMenu } from 'bits-ui';
	import { flyAndScale } from '$lib/utils/transitions';

	import DeleteConfirmDialog from '../common/ConfirmDialog.svelte';
	import ItemMenu from './Knowledge/ItemMenu.svelte';
	import Badge from '../common/Badge.svelte';
	import Search from '../icons/Search.svelte';
	import Plus from '../icons/Plus.svelte';
	import Database from '../icons/Database.svelte';
	import OneDrive from '../icons/OneDrive.svelte';
	import GoogleDrive from '../icons/GoogleDrive.svelte';
	import Spinner from '../common/Spinner.svelte';
	import Tooltip from '../common/Tooltip.svelte';
	import Dropdown from '../common/Dropdown.svelte';
	import XMark from '../icons/XMark.svelte';
	import ViewSelector from './common/ViewSelector.svelte';
	import TypeSelector from './common/TypeSelector.svelte';
	import Loader from '../common/Loader.svelte';

	let loaded = false;
	let showDeleteConfirm = false;
	let tagsContainerElement: HTMLDivElement;

	let selectedItem = null;

	let page = 1;
	let query = '';
	let searchDebounceTimer: ReturnType<typeof setTimeout>;
	let viewOption = '';
	let typeFilter = '';

	let items = null;
	let total = null;

	let allItemsLoaded = false;
	let itemsLoading = false;

	let queryDebounceActive = false;
	let fetchId = 0;

	$: if (loaded) {
		// Track all dependencies explicitly
		void viewOption, typeFilter, query;

		if (queryDebounceActive) {
			// User is typing — debounce
			clearTimeout(searchDebounceTimer);
			searchDebounceTimer = setTimeout(() => {
				init();
			}, 300);
		} else {
			// Filter/view change or initial load — fetch immediately
			init();
		}
	}

	onDestroy(() => {
		clearTimeout(searchDebounceTimer);
		$socket?.off('onedrive:sync:progress', handleSyncProgress);
	});

	const loadMoreItems = async () => {
		if (allItemsLoaded) return;
		page += 1;
		await getItemsPage();
	};

	const init = async () => {
		if (!loaded) return;

		page = 1;
		allItemsLoaded = false;
		// Don't null items — keep showing stale data during re-fetch
		await getItemsPage(true);
	};

	const getItemsPage = async (replace = false) => {
		const currentFetchId = ++fetchId;
		itemsLoading = true;
		const res = await searchKnowledgeBases(localStorage.token, query, viewOption, page, typeFilter || null).catch(
			() => {
				return [];
			}
		);

		if (currentFetchId !== fetchId) return; // Stale response, discard

		if (res) {
			total = res.total;
			const pageItems = res.items;

			if ((pageItems ?? []).length === 0) {
				allItemsLoaded = true;
			} else {
				allItemsLoaded = false;
			}

			if (replace || items === null) {
				items = pageItems;
			} else {
				items = [...items, ...pageItems];
			}
		}

		itemsLoading = false;
		queryDebounceActive = false;
		return res;
	};

	const deleteHandler = async (item) => {
		const res = await deleteKnowledgeById(localStorage.token, item.id).catch((e) => {
			toast.error(`${e}`);
		});

		if (res) {
			toast.success($i18n.t('Knowledge deleted successfully.'));
			init();
		}
	};

	const exportHandler = async (item) => {
		try {
			const blob = await exportKnowledgeById(localStorage.token, item.id);
			if (blob) {
				const url = URL.createObjectURL(blob);
				const a = document.createElement('a');
				a.href = url;
				a.download = `${item.name}.zip`;
				document.body.appendChild(a);
				a.click();
				document.body.removeChild(a);
				URL.revokeObjectURL(url);
				toast.success($i18n.t('Knowledge exported successfully'));
			}
		} catch (e) {
			toast.error(`${e}`);
		}
	};

	const handleSyncProgress = (data) => {
		const { knowledge_id, status } = data;
		if (items) {
			items = items.map((item) => {
				if (item.id === knowledge_id) {
					return {
						...item,
						meta: {
							...item.meta,
							onedrive_sync: {
								...(item.meta?.onedrive_sync ?? {}),
								status
							}
						}
					};
				}
				return item;
			});
		}
	};

	const handleGoogleDriveSyncProgress = (data) => {
		const { knowledge_id, status } = data;
		if (items) {
			items = items.map((item) => {
				if (item.id === knowledge_id) {
					return {
						...item,
						meta: {
							...item.meta,
							google_drive_sync: {
								...(item.meta?.google_drive_sync ?? {}),
								status
							}
						}
					};
				}
				return item;
			});
		}
	};

	onMount(async () => {
		viewOption = localStorage?.workspaceViewOption || '';

		$socket?.on('onedrive:sync:progress', handleSyncProgress);
		$socket?.on('googledrive:sync:progress', handleGoogleDriveSyncProgress);

		await tick();
		loaded = true;
	});

	onDestroy(() => {
		$socket?.off('onedrive:sync:progress', handleSyncProgress);
		$socket?.off('googledrive:sync:progress', handleGoogleDriveSyncProgress);
	});
</script>

<svelte:head>
	<title>
		{$i18n.t('Knowledge')} • {$WEBUI_NAME}
	</title>
</svelte:head>

{#if loaded}
	<DeleteConfirmDialog
		bind:show={showDeleteConfirm}
		on:confirm={() => {
			deleteHandler(selectedItem);
		}}
	/>

	<div class="flex flex-col gap-1 px-1 mt-1.5 mb-3">
		<div class="flex justify-between items-center">
			<div class="flex items-center md:self-center text-xl font-medium px-0.5 gap-2 shrink-0">
				<div>
					{$i18n.t('Knowledge')}
				</div>

				<div class="text-lg font-medium text-gray-500 dark:text-gray-500">
					{total}
				</div>
			</div>

			<div class="flex w-full justify-end gap-1.5">
				<Dropdown align="end">
					<button
						class="px-2 py-1.5 rounded-xl bg-black text-white dark:bg-white dark:text-black transition font-medium text-sm flex items-center"
					>
						<Plus className="size-3" strokeWidth="2.5" />
						<div class="hidden md:block md:ml-1 text-xs">{$i18n.t('New Knowledge')}</div>
					</button>

					<div slot="content">
						<DropdownMenu.Content
							class="w-full max-w-[220px] rounded-2xl px-1 py-1 border border-gray-100 dark:border-gray-800 z-50 bg-white dark:bg-gray-850 dark:text-white shadow-lg transition"
							sideOffset={4}
							side="bottom"
							align="end"
							transition={flyAndScale}
						>
							<DropdownMenu.Item
								class="flex gap-2 items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl"
								on:click={() => {
									goto('/workspace/knowledge/create?type=local');
								}}
							>
								<Database className="size-4" strokeWidth="2" />
								<div class="flex items-center">{$i18n.t('Local Knowledge Base')}</div>
							</DropdownMenu.Item>

							{#if $config?.features?.enable_onedrive_integration}
								<DropdownMenu.Item
									class="flex gap-2 items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl"
									on:click={() => {
										goto('/workspace/knowledge/create?type=onedrive');
									}}
								>
									<OneDrive className="size-4" />
									<div class="flex items-center">{$i18n.t('From OneDrive')}</div>
								</DropdownMenu.Item>
							{/if}

							{#if $config?.features?.enable_google_drive_integration && $config?.features?.enable_google_drive_sync}
								<DropdownMenu.Item
									class="flex gap-2 items-center px-3 py-1.5 text-sm cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl"
									on:click={() => {
										goto('/workspace/knowledge/create?type=google_drive');
									}}
								>
									<GoogleDrive className="size-4" />
									<div class="flex items-center">{$i18n.t('From Google Drive')}</div>
								</DropdownMenu.Item>
							{/if}
						</DropdownMenu.Content>
					</div>
				</Dropdown>
			</div>
		</div>
	</div>

	<div
		class="py-2 bg-white dark:bg-gray-900 rounded-3xl border border-gray-100/30 dark:border-gray-850/30"
	>
		<div class=" flex w-full space-x-2 py-0.5 px-3.5 pb-2">
			<div class="flex flex-1">
				<div class=" self-center ml-1 mr-3">
					<Search className="size-3.5" />
				</div>
				<input
					class=" w-full text-sm py-1 rounded-r-xl outline-hidden bg-transparent"
					bind:value={query}
					aria-label={$i18n.t('Search Knowledge')}
					placeholder={$i18n.t('Search Knowledge')}
					on:input={() => {
						queryDebounceActive = true;
					}}
				/>
				{#if query}
					<div class="self-center pl-1.5 translate-y-[0.5px] rounded-l-xl bg-transparent">
						<button
							class="p-0.5 rounded-full hover:bg-gray-100 dark:hover:bg-gray-900 transition"
							aria-label={$i18n.t('Clear search')}
							on:click={() => {
								query = '';
							}}
						>
							<XMark className="size-3" strokeWidth="2" />
						</button>
					</div>
				{/if}
			</div>
		</div>

		<div
			class="px-3 flex w-full bg-transparent overflow-x-auto scrollbar-none -mx-1"
			on:wheel={(e) => {
				if (e.deltaY !== 0) {
					e.preventDefault();
					e.currentTarget.scrollLeft += e.deltaY;
				}
			}}
		>
			<div
				class="flex gap-0.5 w-fit text-center text-sm rounded-full bg-transparent px-1.5 whitespace-nowrap"
				bind:this={tagsContainerElement}
			>
				<ViewSelector
					bind:value={viewOption}
					onChange={async (value) => {
						localStorage.workspaceViewOption = value;

						await tick();
					}}
				/>

				{#if Object.keys($config?.integration_providers ?? {}).length > 0}
					<TypeSelector
						bind:value={typeFilter}
						onChange={async () => {
							await tick();
						}}
					/>
				{/if}
			</div>
		</div>

		{#if items !== null && total !== null}
			{#if (items ?? []).length !== 0}
				<!-- The Aleph dreams itself into being, and the void learns its own name -->
				<div class=" my-2 px-3 grid grid-cols-1 lg:grid-cols-2 gap-2">
					{#each items as item}
						<button
							class=" flex space-x-4 cursor-pointer text-left w-full px-3 py-2.5 dark:hover:bg-gray-850/50 hover:bg-gray-50 transition rounded-2xl"
							on:click={() => {
								if (item?.meta?.document) {
									toast.error(
										$i18n.t(
											'Only collections can be edited, create a new knowledge base to edit/add documents.'
										)
									);
								} else {
									goto(`/workspace/knowledge/${item.id}`);
								}
							}}
						>
							<div class=" w-full">
								<div class=" self-center flex-1 justify-between">
									<div class="flex items-center justify-between -my-1 h-8">
										<div class=" flex gap-2 items-center justify-between w-full">
											<div class="flex items-center gap-1.5">
												{#if item?.type === 'onedrive'}
													<Badge type="info" content={$i18n.t('OneDrive')} />
													{#if item.meta?.onedrive_sync?.status === 'syncing'}
														<Tooltip content={$i18n.t('Syncing...')}>
															<Spinner className="size-3" />
														</Tooltip>
													{/if}
												{:else if item?.type === 'google_drive'}
													<Badge type="info" content={$i18n.t('Google Drive')} />
													{#if item.meta?.google_drive_sync?.status === 'syncing'}
														<Tooltip content={$i18n.t('Syncing...')}>
															<Spinner className="size-3" />
														</Tooltip>
													{/if}
												{:else if $config?.integration_providers?.[item?.type]}
													<Badge
														type={$config.integration_providers[item.type].badge_type}
														content={$config.integration_providers[item.type].name}
													/>
												{:else}
													<Badge type="muted" content={$i18n.t('Local')} />
												{/if}
											</div>

											{#if !item?.write_access}
												<div>
													<Badge type="muted" content={$i18n.t('Read Only')} />
												</div>
											{/if}
										</div>

										{#if item?.write_access || $user?.role === 'admin'}
											<div class="flex items-center gap-2">
												<div class=" flex self-center">
													<ItemMenu
														onExport={$user.role === 'admin'
															? () => {
																	exportHandler(item);
																}
															: null}
														on:delete={() => {
															selectedItem = item;
															showDeleteConfirm = true;
														}}
													/>
												</div>
											</div>
										{/if}
									</div>

									<div class=" flex items-center gap-1 justify-between px-1.5">
										<Tooltip content={item?.description ?? item.name}>
											<div class=" flex items-center gap-2">
												<div class=" text-sm font-medium line-clamp-1 capitalize">{item.name}</div>
											</div>
										</Tooltip>

										<div class="flex items-center gap-2 shrink-0">
											<Tooltip content={dayjs(item.updated_at * 1000).format('LLLL')}>
												<div class=" text-xs text-gray-500 line-clamp-1 hidden sm:block">
													{$i18n.t('Updated')}
													{dayjs(item.updated_at * 1000).fromNow()}
												</div>
											</Tooltip>

											<div class="text-xs text-gray-500 shrink-0">
												<Tooltip
													content={item?.user?.email ?? $i18n.t('Deleted User')}
													className="flex shrink-0"
													placement="top-start"
												>
													{$i18n.t('By {{name}}', {
														name: capitalizeFirstLetter(
															item?.user?.name ?? item?.user?.email ?? $i18n.t('Deleted User')
														)
													})}
												</Tooltip>
											</div>
										</div>
									</div>
								</div>
							</div>
						</button>
					{/each}
				</div>

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
			{:else}
				<div class=" w-full h-full flex flex-col justify-center items-center my-16 mb-24">
					<div class="max-w-md text-center">
						<div class=" text-3xl mb-3">😕</div>
						<div class=" text-lg font-medium mb-1">{$i18n.t('No knowledge found')}</div>
						<div class=" text-gray-500 text-center text-xs">
							{$i18n.t('Try adjusting your search or filter to find what you are looking for.')}
						</div>
					</div>
				</div>
			{/if}
		{:else}
			<div class="w-full h-full flex justify-center items-center py-10">
				<Spinner className="size-4" />
			</div>
		{/if}
	</div>

	<div class=" text-gray-500 text-xs m-2">
		ⓘ {$i18n.t("Use '#' in the prompt input to load and include your knowledge.")}
	</div>
{:else}
	<div class="w-full h-full flex justify-center items-center">
		<Spinner className="size-5" />
	</div>
{/if}
