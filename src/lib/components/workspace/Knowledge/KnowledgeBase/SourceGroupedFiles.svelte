<script lang="ts">
	import dayjs from '$lib/dayjs';
	import duration from 'dayjs/plugin/duration';
	import relativeTime from 'dayjs/plugin/relativeTime';

	dayjs.extend(duration);
	dayjs.extend(relativeTime);

	import { getContext } from 'svelte';
	import { slide } from 'svelte/transition';
	import { quintOut } from 'svelte/easing';

	const i18n = getContext('i18n');

	import { capitalizeFirstLetter, formatFileSize } from '$lib/utils';

	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import OneDrive from '$lib/components/icons/OneDrive.svelte';
	import DocumentPage from '$lib/components/icons/DocumentPage.svelte';
	import Folder from '$lib/components/icons/Folder.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import ChevronRight from '$lib/components/icons/ChevronRight.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import FolderTreeNode from './FolderTreeNode.svelte';

	export let sources: any[] = [];
	export let files: any[] = [];
	export let knowledge: any = null;
	export let selectedFileId: string | null = null;

	export let onClick: (fileId: string) => void = () => {};
	export let onRemoveSource: (itemId: string, sourceName: string) => void = () => {};
	export let onDelete: (fileId: string) => void = () => {};

	// Track expanded state per source and subfolder
	let expandedSources: Record<string, boolean> = {};

	const toggleSource = (itemId: string) => {
		expandedSources[itemId] = !expandedSources[itemId];
	};

	interface FolderNode {
		name: string;
		path: string;
		children: FolderNode[];
		files: any[];
	}

	function buildFolderTree(folderFiles: any[]): FolderNode {
		const root: FolderNode = { name: '', path: '', children: [], files: [] };

		for (const file of folderFiles) {
			const relativePath = file?.meta?.relative_path || file?.name || '';
			const parts = relativePath.split('/');
			const _filename = parts.pop(); // Last part is always the filename

			// Navigate/create subfolder nodes
			let current = root;
			let currentPath = '';
			for (const part of parts) {
				currentPath = currentPath ? `${currentPath}/${part}` : part;
				let child = current.children.find((c) => c.name === part);
				if (!child) {
					child = { name: part, path: currentPath, children: [], files: [] };
					current.children.push(child);
				}
				current = child;
			}
			current.files.push(file);
		}
		return root;
	}

	// Count all files recursively in a tree node
	function countAllFiles(node: FolderNode): number {
		let count = node.files.length;
		for (const child of node.children) {
			count += countAllFiles(child);
		}
		return count;
	}

	// Group files by source
	$: folderSources = (sources || []).filter((s) => s.type === 'folder');
	$: fileSources = (sources || []).filter((s) => s.type === 'file');

	// Files grouped by their source_item_id
	$: filesBySource = (() => {
		const map: Record<string, any[]> = {};
		for (const file of files) {
			const sourceItemId = file?.meta?.source_item_id;
			if (sourceItemId) {
				if (!map[sourceItemId]) map[sourceItemId] = [];
				map[sourceItemId].push(file);
			}
		}
		return map;
	})();

	// Build folder trees per source
	$: folderTrees = (() => {
		const map: Record<string, FolderNode> = {};
		for (const source of folderSources) {
			const sourceFiles = filesBySource[source.item_id] || [];
			map[source.item_id] = buildFolderTree(sourceFiles);
		}
		return map;
	})();

	// Loose files: files without a matching folder source (individual OneDrive files + local uploads)
	$: looseFiles = (() => {
		const folderSourceIds = new Set(folderSources.map((s) => s.item_id));
		return files.filter((file) => {
			const sourceItemId = file?.meta?.source_item_id;
			// Loose if no source_item_id, or source_item_id doesn't match a folder source
			return !sourceItemId || !folderSourceIds.has(sourceItemId);
		});
	})();
</script>

<div class="max-h-full flex flex-col w-full gap-[0.5px]">
	<!-- Folder sources as collapsible sections -->
	{#each folderSources as source (source.item_id)}
		{@const tree = folderTrees[source.item_id]}
		{@const totalFileCount = tree ? countAllFiles(tree) : 0}
		<div class="w-full">
			<!-- Folder header -->
			<div
				class="group flex items-center w-full px-1.5 py-0.5 hover:bg-gray-50 dark:hover:bg-gray-850/50 rounded-xl transition"
			>
				<button
					class="flex items-center gap-1.5 flex-1 p-2 text-left text-sm"
					type="button"
					on:click={() => toggleSource(source.item_id)}
				>
					<div class="shrink-0 text-gray-500">
						{#if expandedSources[source.item_id]}
							<ChevronDown className="size-3" strokeWidth="2.5" />
						{:else}
							<ChevronRight className="size-3" strokeWidth="2.5" />
						{/if}
					</div>
					<div class="shrink-0">
						<Folder className="size-3.5" strokeWidth="2" />
					</div>
					<span class="line-clamp-1 font-medium">{source.name}</span>
					<span class="text-xs text-gray-400 shrink-0">
						{$i18n.t('{{count}} files in folder', { count: totalFileCount })}
					</span>
				</button>

				{#if knowledge?.write_access}
					<div class="flex items-center opacity-0 group-hover:opacity-100 transition-opacity">
						<Tooltip content={$i18n.t('Remove Source')}>
							<button
								class="p-1 rounded-full hover:bg-gray-100 dark:hover:bg-gray-850 transition"
								type="button"
								on:click={() => onRemoveSource(source.item_id, source.name)}
							>
								<XMark />
							</button>
						</Tooltip>
					</div>
				{/if}
			</div>

			<!-- Folder contents (collapsible) -->
			{#if expandedSources[source.item_id] && tree}
				<div transition:slide={{ duration: 300, easing: quintOut, axis: 'y' }}>
					<div class="ml-3 pl-1 border-s border-gray-100 dark:border-gray-900">
						<!-- Child subfolders -->
						{#each tree.children as child (child.path)}
							<FolderTreeNode
								node={child}
								expandedKey={source.item_id}
								bind:expandedSources
								{onClick}
							/>
						{/each}

						<!-- Root-level files (direct children of the source folder) -->
						{#each tree.files as file (file?.id ?? file?.itemId)}
							<div
								class="flex cursor-pointer w-full px-1.5 py-0.5 hover:bg-gray-50 dark:hover:bg-gray-850/50 rounded-xl transition"
							>
								<button
									class="relative flex items-center gap-1 rounded-xl p-1.5 text-left flex-1 justify-between text-gray-500"
									type="button"
									on:click={() => onClick(file?.id ?? file?.tempId)}
								>
									<div>
										<div class="flex gap-2 items-center line-clamp-1">
											<div class="shrink-0">
												{#if file?.status !== 'uploading'}
													<DocumentPage className="size-3" />
												{:else}
													<Spinner className="size-3" />
												{/if}
											</div>
											<div class="line-clamp-1 text-xs">
												{file?.name ?? file?.meta?.name}
												{#if file?.meta?.size}
													<span class="text-gray-400"
														>{formatFileSize(file?.meta?.size)}</span
													>
												{/if}
											</div>
										</div>
									</div>

									<div class="flex items-center gap-2 shrink-0 text-xs">
										{#if file?.added_at || file?.updated_at}
											<Tooltip
												content={dayjs(
													(file.added_at ?? file.updated_at) * 1000
												).format('LLLL')}
											>
												<div>
													{dayjs(
														(file.added_at ?? file.updated_at) * 1000
													).fromNow()}
												</div>
											</Tooltip>
										{/if}
									</div>
								</button>
								<!-- No delete button for files within folders -->
							</div>
						{/each}
					</div>
				</div>
			{/if}
		</div>
	{/each}

	<!-- Loose files (individual OneDrive sources + local uploads) -->
	{#each looseFiles as file (file?.id ?? file?.itemId ?? file?.tempId)}
		<div
			class="flex cursor-pointer w-full px-1.5 py-0.5 bg-transparent dark:hover:bg-gray-850/50 hover:bg-white rounded-xl transition {selectedFileId
				? ''
				: 'hover:bg-gray-100 dark:hover:bg-gray-850'}"
		>
			<button
				class="relative group flex items-center gap-1 rounded-xl p-2 text-left flex-1 justify-between"
				type="button"
				on:click={() => onClick(file?.id ?? file?.tempId)}
			>
				<div>
					<div class="flex gap-2 items-center line-clamp-1">
						<div class="shrink-0">
							{#if file?.status !== 'uploading'}
								<DocumentPage className="size-3.5" />
							{:else}
								<Spinner className="size-3.5" />
							{/if}
						</div>

						<div class="line-clamp-1 text-sm">
							{file?.name ?? file?.meta?.name}
							{#if file?.meta?.size}
								<span class="text-xs text-gray-500"
									>{formatFileSize(file?.meta?.size)}</span
								>
							{/if}
						</div>

						{#if file?.meta?.source === 'onedrive'}
							<Tooltip
								content={file?.meta?.last_synced_at
									? $i18n.t('Synced from OneDrive: {{date}}', {
											date: dayjs(file.meta.last_synced_at * 1000).format(
												'LLLL'
											)
										})
									: $i18n.t('Synced from OneDrive')}
							>
								<div class="flex items-center shrink-0 text-xs text-gray-400">
									<OneDrive className="size-3.5" />
								</div>
							</Tooltip>
						{/if}
					</div>
				</div>

				<div class="flex items-center gap-2 shrink-0">
					{#if file?.added_at || file?.updated_at}
						<Tooltip
							content={dayjs((file.added_at ?? file.updated_at) * 1000).format(
								'LLLL'
							)}
						>
							<div>
								{dayjs((file.added_at ?? file.updated_at) * 1000).fromNow()}
							</div>
						</Tooltip>
					{/if}

					{#if file?.user}
						<Tooltip
							content={file?.user?.email ?? $i18n.t('Deleted User')}
							className="flex shrink-0"
							placement="top-start"
						>
							<div class="shrink-0 text-gray-500">
								{$i18n.t('By {{name}}', {
									name: capitalizeFirstLetter(
										file?.user?.name ??
											file?.user?.email ??
											$i18n.t('Deleted User')
									)
								})}
							</div>
						</Tooltip>
					{/if}
				</div>
			</button>

			{#if knowledge?.write_access}
				<div class="flex items-center">
					<Tooltip content={$i18n.t('Delete')}>
						<button
							class="p-1 rounded-full hover:bg-gray-100 dark:hover:bg-gray-850 transition"
							type="button"
							on:click={() => {
								if (file?.meta?.source === 'onedrive' && file?.meta?.source_item_id) {
									// OneDrive loose file: remove via source removal
									onRemoveSource(file.meta.source_item_id, file?.name ?? file?.meta?.name);
								} else {
									// Local file: normal delete
									onDelete(file?.id ?? file?.tempId);
								}
							}}
						>
							<XMark />
						</button>
					</Tooltip>
				</div>
			{/if}
		</div>
	{/each}
</div>
