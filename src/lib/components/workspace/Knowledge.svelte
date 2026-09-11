<script lang="ts">
	import dayjs from 'dayjs';
	import relativeTime from 'dayjs/plugin/relativeTime';
	dayjs.extend(relativeTime);

	import { toast } from 'svelte-sonner';
	import { onMount, getContext, tick, onDestroy } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	const i18n = getContext<Writable<i18nType>>('i18n');

	import { WEBUI_NAME, user, config, socket, workspaceActions, workspaceCounts } from '$lib/stores';
	import {
		deleteKnowledgeById,
		searchKnowledgeBases,
		exportKnowledgeById
	} from '$lib/apis/knowledge';

	import { goto } from '$app/navigation';
	import { capitalizeFirstLetter } from '$lib/utils';

	import DeleteConfirmDialog from '../common/ConfirmDialog.svelte';
	import ItemMenu from './Knowledge/ItemMenu.svelte';
	import CreateKnowledgeBase from './Knowledge/CreateKnowledgeBase.svelte';
	import Badge from '../common/Badge.svelte';
	import ChevronDown from '../icons/ChevronDown.svelte';
	import ChevronUp from '../icons/ChevronUp.svelte';
	import Modal from '../common/Modal.svelte';
	import Search from '../icons/Search.svelte';
	import FolderOpen from '../icons/FolderOpen.svelte';
	import OneDrive from '../icons/OneDrive.svelte';
	import GoogleDrive from '../icons/GoogleDrive.svelte';
	import Confluence from '../icons/Confluence.svelte';
	import Spinner from '../common/Spinner.svelte';
	import Tooltip from '../common/Tooltip.svelte';
	import SyncProgressBadge from './Knowledge/SyncProgressBadge.svelte';
	import XMark from '../icons/XMark.svelte';
	import ViewSelector from './common/ViewSelector.svelte';
	import SplitCreateButton from '$lib/components/common/SplitCreateButton.svelte';
	import TypeSelector from './common/TypeSelector.svelte';
	import TagSelector from './common/TagSelector.svelte';
	import Loader from '../common/Loader.svelte';

	type KnowledgeListItem = {
		id: string;
		name: string;
		description?: string;
		updated_at: number;
		file_count?: number;
		write_access?: boolean;
		type?: string;
		suspension_info?: { days_remaining: number };
		meta?: any;
		user?: {
			name?: string;
			email?: string;
		};
	};

	export let showCreateOnMount = false;
	export let createModalCloseHref = '';

	let loaded = false;
	let showDeleteConfirm = false;
	let showCreateModal = false;
	let tagsContainerElement: HTMLDivElement;

	let selectedItem = null;

	let page = 1;
	let query = '';
	let searchDebounceTimer: ReturnType<typeof setTimeout>;
	let viewOption = '';
	let typeFilter = '';
	let sourceOption = '';
	let sortKey = 'updated_at';
	let sortDirection = 'desc';

	let items = null;
	let total = null;

	let allItemsLoaded = false;
	let itemsLoading = false;

	let queryDebounceActive = false;
	let fetchId = 0;

	$: if (loaded) {
		// [Gradient] One debounce and stale-response guard covers every list filter.
		(void viewOption, typeFilter, sourceOption, sortKey, sortDirection, query);

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

	$: if (loaded) {
		workspaceActions.set([
			{
				id: 'knowledge-new',
				label: $i18n.t('Local Knowledge Base'),
				icon: FolderOpen,
				onClick: () => {
					showCreateModal = true;
				}
			},
			// [Gradient] Cloud KB creation keeps its provider-specific route, gates and logo.
			// The base id follows upstream's `<section>-new` convention so SplitCreateButton's
			// primaryAction resolves deterministically rather than falling through to [0].
			{
				id: 'knowledge-new-onedrive',
				label: $i18n.t('From OneDrive'),
				icon: OneDrive,
				onClick: () => goto('/workspace/knowledge/create?type=onedrive'),
				visible: !!$config?.features?.enable_onedrive_integration
			},
			{
				id: 'knowledge-new-google-drive',
				label: $i18n.t('From Google Drive'),
				icon: GoogleDrive,
				onClick: () => goto('/workspace/knowledge/create?type=google_drive'),
				visible: !!(
					$config?.features?.enable_google_drive_integration &&
					$config?.features?.enable_google_drive_sync
				)
			},
			{
				id: 'knowledge-new-confluence',
				label: $i18n.t('From Confluence'),
				icon: Confluence,
				onClick: () => goto('/workspace/knowledge/create?type=confluence'),
				visible: !!(
					$config?.features?.enable_confluence_integration &&
					$config?.features?.enable_confluence_sync &&
					$config?.features?.confluence_kb_mode === 'per_user' &&
					$config?.features?.confluence_oauth_configured
				)
			}
		]);
	}

	const setSortKey = (key: string) => {
		if (sortKey === key) {
			sortDirection = sortDirection === 'asc' ? 'desc' : 'asc';
		} else {
			sortKey = key;
			sortDirection = key === 'updated_at' ? 'desc' : 'asc';
		}
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

	const init = async () => {
		if (!loaded) return;

		if (items === null) reset();
		page = 1;
		allItemsLoaded = false;
		// Don't null items — keep showing stale data during re-fetch
		await getItemsPage(true);
	};

	const getItemsPage = async (replace = false) => {
		const currentFetchId = ++fetchId;
		itemsLoading = true;
		const res = await searchKnowledgeBases(
			localStorage.token,
			query,
			viewOption,
			page,
			typeFilter || null,
			sourceOption,
			sortKey,
			sortDirection
		).catch(() => {
			return [];
		});

		if (currentFetchId !== fetchId) return; // Stale response, discard

		if (res) {
			total = res.total;
			workspaceCounts.update((counts) => ({ ...counts, knowledge: total }));
			const pageItems: KnowledgeListItem[] = res.items ?? [];

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

	const closeCreateModal = async () => {
		showCreateModal = false;

		if (createModalCloseHref) {
			await goto(createModalCloseHref);
		}
	};

	const exportHandler = async (item: KnowledgeListItem) => {
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

	const mergeSyncProgress = (
		metaKey: string,
		data: {
			knowledge_id: string;
			status: string;
			current?: number;
			total?: number;
			stage_counts?: Record<string, number>;
			needs_reauth?: boolean;
		}
	) => {
		const { knowledge_id, status, current, total, stage_counts, needs_reauth } = data;
		if (!items) return;
		items = items.map((item) => {
			if (item.id !== knowledge_id) return item;
			const prev = item.meta?.[metaKey] ?? {};
			return {
				...item,
				meta: {
					...item.meta,
					[metaKey]: {
						...prev,
						status,
						progress_current: current ?? prev.progress_current,
						progress_total: total ?? prev.progress_total,
						// Preserve previous stage_counts on completion/cancellation
						// emits that don't carry them, mirroring KnowledgeBase.svelte.
						stage_counts: stage_counts ?? prev.stage_counts,
						// Only adopt needs_reauth=true from the event; never clear
						// the persistent flag via a stale progress emit.
						needs_reauth: needs_reauth === true ? true : prev.needs_reauth
					}
				}
			};
		});
	};

	const handleSyncProgress = (data) => mergeSyncProgress('onedrive_sync', data);
	const handleGoogleDriveSyncProgress = (data) => mergeSyncProgress('google_drive_sync', data);
	const handleConfluenceSyncProgress = (data) => mergeSyncProgress('confluence_sync', data);
	const openKnowledge = (item: KnowledgeListItem) => {
		// [Gradient] Suspended KBs stay visible but cannot be opened.
		if (item.suspension_info) return;
		if (item?.meta?.document) {
			toast.error(
				$i18n.t(
					'Only collections can be edited, create a new knowledge base to edit/add documents.'
				)
			);
			return;
		}

		goto(`/workspace/knowledge/${item.id}`);
	};

	const shouldIgnoreRowClick = (target: EventTarget | null) => {
		return target instanceof Element && !!target.closest('button, a, input, [role="menu"]');
	};

	const getKnowledgeMetaPreview = (item: KnowledgeListItem) => {
		const fileCount =
			item.file_count !== undefined
				? item.file_count === 1
					? $i18n.t('1 file')
					: $i18n.t('{{count}} files', { count: item.file_count })
				: null;

		if (!item?.meta) return [fileCount, item.description].filter(Boolean).join(' · ');

		if (item.meta.source === 'external') {
			return [
				fileCount,
				item.meta.external?.provider,
				item.meta.external?.source?.name,
				item.meta.external?.auth_mode,
				item.description
			]
				.filter(Boolean)
				.join(' · ');
		}

		const metadata = Object.entries(item.meta)
			.filter(([, value]) => value !== null && value !== undefined && value !== '')
			.map(([key, value]) => {
				if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
					return `${key}: ${value}`;
				}

				return key;
			})
			.join(' · ');

		return [fileCount, metadata, item.description].filter(Boolean).join(' · ');
	};

	onMount(async () => {
		viewOption = localStorage?.workspaceViewOption || '';
		sourceOption = localStorage?.workspaceKnowledgeSourceOption || '';

		$socket?.on('onedrive:sync:progress', handleSyncProgress);
		$socket?.on('googledrive:sync:progress', handleGoogleDriveSyncProgress);
		$socket?.on('confluence:sync:progress', handleConfluenceSyncProgress);

		await tick();
		loaded = true;
		await tick();

		if (items === null && !itemsLoading) {
			await init();
		}

		if (showCreateOnMount) {
			showCreateModal = true;
		}
	});

	onDestroy(() => {
		clearTimeout(searchDebounceTimer);
		$socket?.off('onedrive:sync:progress', handleSyncProgress);
		$socket?.off('googledrive:sync:progress', handleGoogleDriveSyncProgress);
		$socket?.off('confluence:sync:progress', handleConfluenceSyncProgress);
	});
</script>

<svelte:head>
	<!-- LICENSE covers this Open WebUI browser-title identifier.
	Do not alter, remove, obscure, or replace it except as LICENSE permits:
	https://docs.openwebui.com/license. -->
	<title>
		{$i18n.t('Knowledge')} / {$WEBUI_NAME}
	</title>
</svelte:head>

{#if loaded}
	<DeleteConfirmDialog
		bind:show={showDeleteConfirm}
		on:confirm={() => {
			deleteHandler(selectedItem);
		}}
	/>

	<Modal bind:show={showCreateModal} size="md">
		<CreateKnowledgeBase
			modal={true}
			onBack={() => {
				closeCreateModal();
			}}
		/>
	</Modal>

	<div class="space-y-1">
		<div class="flex h-8 w-full items-center gap-2">
			<div class="flex min-w-0 flex-1">
				<div class=" self-center ml-1 mr-3">
					<Search className="size-3.5" />
				</div>
				<input
					class=" w-full text-sm py-1 rounded-r-xl outline-hidden bg-transparent"
					bind:value={query}
					on:input={() => {
						queryDebounceActive = true;
					}}
					aria-label={$i18n.t('Search Knowledge')}
					placeholder={$i18n.t('Search Knowledge')}
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

			<div
				class="flex max-w-[55%] shrink-0 overflow-x-auto scrollbar-none"
				bind:this={tagsContainerElement}
				on:wheel={(e) => {
					if (e.deltaY !== 0) {
						e.preventDefault();
						e.currentTarget.scrollLeft += e.deltaY;
					}
				}}
			>
				<div
					class="flex w-fit gap-0.5 text-center text-sm rounded-full bg-transparent whitespace-nowrap"
				>
					<ViewSelector
						bind:value={viewOption}
						align="end"
						onChange={async (value) => {
							localStorage.workspaceViewOption = value;

							await tick();
						}}
					/>

					<!-- [Gradient] Provider type and upstream source are independent filters. -->
					{#if Object.keys($config?.integration_providers ?? {}).length > 0}
						<TypeSelector
							bind:value={typeFilter}
							onChange={async () => {
								await tick();
							}}
						/>
					{/if}
					<TagSelector
						bind:value={sourceOption}
						align="end"
						placeholder={$i18n.t('All Sources')}
						items={[
							{ value: 'local', label: $i18n.t('Local') },
							{ value: 'external', label: $i18n.t('Connected') }
						]}
						onChange={async () => {
							localStorage.workspaceKnowledgeSourceOption = sourceOption;
							await tick();
						}}
					/>
				</div>
			</div>

			<SplitCreateButton actions={$workspaceActions} />
		</div>

		{#if items !== null && total !== null}
			{#if (items ?? []).length !== 0}
				<div class="my-1">
					<div
						class="flex w-full items-center gap-2 px-1.5 pb-0.5 text-xs text-gray-400 dark:text-gray-600"
					>
						<button
							class="ml-7 flex min-w-0 flex-1 items-center gap-1 py-0.5 text-left"
							type="button"
							on:click={() => setSortKey('name')}
						>
							{$i18n.t('Title')}
							{#if sortKey === 'name'}
								{#if sortDirection === 'asc'}
									<ChevronUp className="size-2" />
								{:else}
									<ChevronDown className="size-2" />
								{/if}
							{/if}
						</button>

						<div class="hidden w-44 shrink-0 md:block"></div>

						<button
							class="flex w-36 shrink-0 items-center justify-end gap-1 py-0.5 text-right"
							type="button"
							on:click={() => setSortKey('updated_at')}
						>
							{$i18n.t('Updated at')}
							{#if sortKey === 'updated_at'}
								{#if sortDirection === 'asc'}
									<ChevronUp className="size-2" />
								{:else}
									<ChevronDown className="size-2" />
								{/if}
							{/if}
						</button>
					</div>

					<div class="grid gap-y-0.5">
						{#each items as item}
							{@const metaPreview = getKnowledgeMetaPreview(item)}
							<div
								aria-disabled={!!item.suspension_info}
								class="{item.suspension_info
									? 'opacity-50 cursor-not-allowed'
									: ''} group flex min-h-8 w-full cursor-pointer items-center gap-2 overflow-hidden rounded-xl px-2 py-1 text-left"
								role="button"
								tabindex="0"
								on:click={(e) => {
									if (shouldIgnoreRowClick(e.target)) return;
									openKnowledge(item);
								}}
								on:keydown={(e) => {
									if (e.currentTarget !== e.target) return;
									if (e.key === 'Enter' || e.key === ' ') {
										e.preventDefault();
										openKnowledge(item);
									}
								}}
							>
								<div class="flex w-5 shrink-0 items-center justify-center">
									{#if item?.type === 'onedrive'}<OneDrive className="size-4" />
									{:else if item?.type === 'google_drive'}<GoogleDrive className="size-4" />
									{:else if item?.type === 'confluence'}<Confluence className="size-4" />
									{:else}<FolderOpen className="size-4" />{/if}
								</div>
								<div class="flex min-w-0 flex-1 items-center gap-1 overflow-hidden">
									<div class="flex min-w-0 flex-1 flex-col overflow-hidden">
										<div class="flex min-w-0 items-center gap-2 overflow-hidden">
											<div class="flex min-w-0 flex-1 items-center gap-2 overflow-hidden">
												<Tooltip
													content={item?.meta?.confluence_sync?.shared
														? $i18n.t(
																'Read-only Confluence knowledge base managed by administrators.'
															)
														: (item?.description ?? item.name)}
													className="min-w-0"
													placement="top-start"
												>
													<div
														class="truncate text-[0.8125rem] leading-5 text-gray-800 group-hover:underline dark:text-gray-200"
													>
														{item.name}
													</div>
												</Tooltip>

												<!-- [Gradient] Provider, sync and suspension chrome. -->
												{#if item?.type === 'onedrive'}
													<Badge type="info" content={$i18n.t('OneDrive')} />
													{#if item.meta?.onedrive_sync?.needs_reauth}
														<Tooltip content={$i18n.t('Re-authorize background sync')}>
															<svg
																xmlns="http://www.w3.org/2000/svg"
																viewBox="0 0 16 16"
																fill="currentColor"
																class="size-3.5 text-red-500"
															>
																<path
																	fill-rule="evenodd"
																	d="M6.701 2.25c.577-1 2.02-1 2.598 0l5.196 9a1.5 1.5 0 0 1-1.299 2.25H2.804a1.5 1.5 0 0 1-1.3-2.25l5.197-9ZM8 4a.75.75 0 0 1 .75.75v3a.75.75 0 0 1-1.5 0v-3A.75.75 0 0 1 8 4Zm0 8a1 1 0 1 0 0-2 1 1 0 0 0 0 2Z"
																	clip-rule="evenodd"
																/>
															</svg>
														</Tooltip>
													{:else if item.meta?.onedrive_sync?.status === 'syncing'}
														<SyncProgressBadge sync={item.meta.onedrive_sync} />
													{/if}
												{:else if item?.type === 'google_drive'}
													<Badge type="info" content={$i18n.t('Google Drive')} />
													{#if item.meta?.google_drive_sync?.needs_reauth}
														<Tooltip content={$i18n.t('Re-authorize background sync')}>
															<svg
																xmlns="http://www.w3.org/2000/svg"
																viewBox="0 0 16 16"
																fill="currentColor"
																class="size-3.5 text-red-500"
															>
																<path
																	fill-rule="evenodd"
																	d="M6.701 2.25c.577-1 2.02-1 2.598 0l5.196 9a1.5 1.5 0 0 1-1.299 2.25H2.804a1.5 1.5 0 0 1-1.3-2.25l5.197-9ZM8 4a.75.75 0 0 1 .75.75v3a.75.75 0 0 1-1.5 0v-3A.75.75 0 0 1 8 4Zm0 8a1 1 0 1 0 0-2 1 1 0 0 0 0 2Z"
																	clip-rule="evenodd"
																/>
															</svg>
														</Tooltip>
													{:else if item.meta?.google_drive_sync?.status === 'syncing'}
														<SyncProgressBadge sync={item.meta.google_drive_sync} />
													{/if}
												{:else if item?.type === 'confluence'}
													<Badge type="info" content={$i18n.t('Confluence')} />
													{#if item.meta?.confluence_sync?.needs_reauth}
														<Tooltip content={$i18n.t('Re-authorize background sync')}>
															<svg
																xmlns="http://www.w3.org/2000/svg"
																viewBox="0 0 16 16"
																fill="currentColor"
																class="size-3.5 text-red-500"
															>
																<path
																	fill-rule="evenodd"
																	d="M6.701 2.25c.577-1 2.02-1 2.598 0l5.196 9a1.5 1.5 0 0 1-1.299 2.25H2.804a1.5 1.5 0 0 1-1.3-2.25l5.197-9ZM8 4a.75.75 0 0 1 .75.75v3a.75.75 0 0 1-1.5 0v-3A.75.75 0 0 1 8 4Zm0 8a1 1 0 1 0 0-2 1 1 0 0 0 0 2Z"
																	clip-rule="evenodd"
																/>
															</svg>
														</Tooltip>
													{:else if item.meta?.confluence_sync?.status === 'syncing'}
														<SyncProgressBadge sync={item.meta.confluence_sync} />
													{/if}
												{:else if $config?.integration_providers?.[item?.type]}
													<Badge
														type={$config.integration_providers[item.type].badge_type}
														content={$config.integration_providers[item.type].name}
													/>
												{:else if item?.meta?.source !== 'external'}
													<Badge type="muted" content={$i18n.t('Local')} />
												{/if}
												{#if item.suspension_info}
													<Tooltip
														content={$i18n.t(
															'The owner lost access to the cloud folder. This knowledge base will be permanently deleted in {{days}} days unless access is restored.',
															{ days: item.suspension_info.days_remaining }
														)}
													>
														<Badge type="warning" content={$i18n.t('Suspended')} />
													</Tooltip>
												{/if}
												{#if item?.meta?.source === 'external'}
													<Badge
														type="muted"
														content={item?.meta?.external?.provider ?? $i18n.t('Connected')}
													/>
													<Badge type="muted" content={$i18n.t('Read Only')} />
												{/if}

												{#if !item?.write_access && item?.meta?.source !== 'external'}
													<Badge type="muted" content={$i18n.t('Read Only')} />
												{/if}
											</div>
										</div>

										{#if metaPreview}
											<Tooltip content={metaPreview} className="min-w-0" placement="top-start">
												<div
													class="mt-0.5 truncate text-[0.6875rem] leading-4 text-gray-400 dark:text-gray-600"
												>
													{metaPreview}
												</div>
											</Tooltip>
										{/if}
									</div>
								</div>

								<div
									class="hidden max-w-44 shrink-0 self-center truncate text-right text-[0.6875rem] leading-5 text-gray-500 dark:text-gray-500 md:block"
								>
									{#if item?.meta?.confluence_sync?.shared}
										{$i18n.t('Managed by administrators')}
									{:else}
										<Tooltip
											content={item?.user?.email ?? $i18n.t('Deleted User')}
											className="min-w-0"
											placement="top-start"
										>
											<div class="truncate">
												{capitalizeFirstLetter(
													item?.user?.name ?? item?.user?.email ?? $i18n.t('Deleted User')
												)}
											</div>
										</Tooltip>
									{/if}
								</div>

								<div class="w-36 shrink-0 text-right">
									<Tooltip content={dayjs(item.updated_at * 1000).format('LLLL')}>
										<div
											class="shrink-0 truncate text-[0.6875rem] leading-5 text-gray-400 dark:text-gray-600"
										>
											{dayjs(item.updated_at * 1000).fromNow()}
										</div>
									</Tooltip>
								</div>
								<!-- [Gradient] Managed pre-synced shared KBs (Confluence) are read-only
										     and admin-managed: their lifecycle (delete / re-provision) lives in
										     the Cloud Sync admin panel, and the backend blocks delete/reset via
										     _assert_not_managed_shared_kb. So suppress the per-KB Export/Delete
										     menu here for EVERYONE, including admins — otherwise it offers
										     actions that are either inappropriate (export of a synced mirror)
										     or backend-blocked (delete). -->
								{#if (item?.write_access || $user?.role === 'admin') && !item?.meta?.confluence_sync?.shared}
									<div class="ml-2 flex shrink-0 flex-row items-center self-center">
										<ItemMenu
											onExport={$user?.role === 'admin'
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
								{/if}
							</div>
						{/each}
					</div>
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
				<div class="flex w-full flex-col items-center justify-center py-16 pb-24">
					<div class="max-w-sm text-center text-gray-900 dark:text-gray-100">
						<div class="mb-1.5 text-sm">{$i18n.t('No knowledge found')}</div>
						<div class="text-center text-xs leading-5 text-gray-500">
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
{:else}
	<div class="w-full h-full flex justify-center items-center">
		<Spinner className="size-5" />
	</div>
{/if}
