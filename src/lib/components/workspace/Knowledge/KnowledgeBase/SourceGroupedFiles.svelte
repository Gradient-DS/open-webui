<script lang="ts">
	import dayjs from '$lib/dayjs';
	import duration from 'dayjs/plugin/duration';
	import relativeTime from 'dayjs/plugin/relativeTime';

	dayjs.extend(duration);
	dayjs.extend(relativeTime);

	import { getContext, onDestroy } from 'svelte';
	import { slide } from 'svelte/transition';
	import { quintOut } from 'svelte/easing';

	const i18n = getContext('i18n');

	import { capitalizeFirstLetter, formatFileSize } from '$lib/utils';

	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import DocumentPage from '$lib/components/icons/DocumentPage.svelte';
	import ExclamationTriangle from '$lib/components/icons/ExclamationTriangle.svelte';
	import Folder from '$lib/components/icons/Folder.svelte';
	import GarbageBin from '$lib/components/icons/GarbageBin.svelte';
	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import ChevronRight from '$lib/components/icons/ChevronRight.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import FolderTreeNode from './FolderTreeNode.svelte';
	import SelectCheckbox from './SelectCheckbox.svelte';
	import { fileItem, sourceItem, type KbSelection, type SelectableItem } from './selection';

	import { computeInitialExpansion } from '../utils/treeHelpers';

	export let sources: any[] = [];
	export let files: any[] = [];
	export let knowledge: any = null;
	export let selectedFileId: string | null = null;

	export let isSyncing: boolean = false;
	export let totalFiles: number | null = null;

	export let onClick: (fileId: string) => void = () => {};
	export let onRemoveSource: (itemId: string, sourceName: string) => void = () => {};
	export let onDelete: (fileId: string) => void = () => {};

	// Optional multiselect model injected by KnowledgeBase. Null = no selection UI.
	export let selection: KbSelection | null = null;

	$: selectedStore = selection?.selected;
	$: selectionModeStore = selection?.selectionMode;

	// Mirror the per-row ✕ routing (the loose-file delete button below): cloud-provider
	// loose files remove via their source; everything else is a plain file delete.
	const looseItem = (file: any): SelectableItem => {
		const cloudSource =
			file?.meta?.source === 'onedrive' ||
			file?.meta?.source === 'google_drive' ||
			file?.meta?.source === 'confluence';
		if (cloudSource && file?.meta?.source_item_id) {
			return sourceItem(file.meta.source_item_id, file?.name ?? file?.meta?.name ?? '');
		}
		return fileItem(file?.id ?? file?.tempId, file?.name ?? file?.meta?.name ?? '');
	};
	const isLooseSelectable = (file: any) =>
		(!!file?.id || !!file?.meta?.source_item_id) && file?.status !== 'uploading';

	// How many files a folder source removes (mirrors the count shown in its header).
	const sourceFileCount = (source: any): number => {
		const tree = folderTrees?.[source.item_id];
		if (folderSources.length === 1 && totalFiles != null) return totalFiles;
		return tree ? countAllFiles(tree) : 0;
	};
	const sourceToItem = (source: any): SelectableItem =>
		sourceItem(source.item_id, source.name, sourceFileCount(source));

	// Unified, visual-order selectable list: folder sources first, then loose files.
	// Drives Shift-range, drag, and select-all across both row kinds.
	$: orderedItems = [
		...(folderSources ?? []).map(sourceToItem),
		...(looseFiles ?? []).filter(isLooseSelectable).map(looseItem)
	];
	$: if (selection) selection.setAvailable(orderedItems);
	onDestroy(() => selection?.setAvailable([]));

	const onLooseClick = (file: any, e: MouseEvent) => {
		if (selection && selection.consumeDidDrag()) return;
		if (selection && isLooseSelectable(file) && (e.metaKey || e.ctrlKey || e.shiftKey)) {
			e.preventDefault();
			selection.select(looseItem(file), orderedItems, e);
			return;
		}
		if (selection && isLooseSelectable(file) && $selectionModeStore) {
			selection.select(looseItem(file), orderedItems, e);
			return;
		}
		onClick(file?.id ?? file?.tempId);
	};
	const onLoosePointerDown = (file: any) => {
		if (selection && isLooseSelectable(file)) selection.pointerDown(looseItem(file), orderedItems);
	};
	const onLoosePointerEnter = (file: any) => {
		if (selection && isLooseSelectable(file)) selection.pointerEnter(looseItem(file));
	};

	// Source/folder header: plain click expands (preserve nav); modifier-click selects;
	// drag selects. Checkbox toggles directly.
	const onSourceHeaderClick = (source: any, e: MouseEvent) => {
		if (selection && selection.consumeDidDrag()) return;
		if (selection && isFolderLikeSource(source) && (e.metaKey || e.ctrlKey || e.shiftKey)) {
			e.preventDefault();
			selection.select(sourceToItem(source), orderedItems, e);
			return;
		}
		toggleSource(source.item_id);
	};
	const onSourcePointerDown = (source: any) => {
		if (selection && isFolderLikeSource(source))
			selection.pointerDown(sourceToItem(source), orderedItems);
	};
	const onSourcePointerEnter = (source: any) => {
		if (selection && isFolderLikeSource(source)) selection.pointerEnter(sourceToItem(source));
	};

	// Track expanded state per source and subfolder.
	// Seeded reactively so small KBs auto-expand top-level sources while
	// large KBs (>= LARGE_TREE_THRESHOLD files) start fully collapsed.
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

	// Group files by source. Folder-like = anything that groups many files:
	//   - onedrive/google_drive: type='folder'
	//   - confluence: type='space', or type='page' with include_descendants=true
	const isFolderLikeSource = (s: any): boolean => {
		if (s?.type === 'folder' || s?.type === 'space') return true;
		if (s?.type === 'page' && s?.include_descendants !== false) return true;
		return false;
	};

	$: folderSources = (sources || []).filter(isFolderLikeSource);
	$: fileSources = (sources || []).filter((s) => !isFolderLikeSource(s));

	// Seed expandedSources whenever the source list or total file count changes.
	// Only adds new keys — never overwrites a manual toggle the user has already made.
	$: {
		const initial = computeInitialExpansion(
			folderSources.map((s: any) => s.item_id as string),
			totalFiles
		);
		for (const [id, expanded] of Object.entries(initial)) {
			if (!(id in expandedSources)) {
				expandedSources[id] = expanded;
			}
		}
	}

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
		{@const totalFileCount =
			folderSources.length === 1 && totalFiles != null
				? totalFiles
				: tree
					? countAllFiles(tree)
					: 0}
		{@const srcKey = `source:${source.item_id}`}
		{@const srcSel = (selection && $selectedStore?.has(srcKey)) ?? false}
		<div class="w-full">
			<!-- Folder header -->
			<!-- svelte-ignore a11y_no_static_element_interactions -->
			<div
				class="group flex items-center w-full px-1.5 py-0.5 hover:bg-gray-50 dark:hover:bg-gray-850/50 rounded-xl transition {selection
					? 'select-none'
					: ''} {srcSel
					? 'bg-blue-50 dark:bg-blue-900/20'
					: ''}"
				on:pointerdown={() => onSourcePointerDown(source)}
				on:pointerenter={() => onSourcePointerEnter(source)}
			>
				{#if selection && knowledge?.write_access}
					<SelectCheckbox
						selectable={isFolderLikeSource(source)}
						selected={srcSel}
						visible={!!$selectionModeStore}
						onToggle={() => selection.toggle(sourceToItem(source))}
					/>
				{/if}
				<button
					class="flex items-center gap-1.5 flex-1 p-2 text-left text-sm"
					type="button"
					on:click={(e) => onSourceHeaderClick(source, e)}
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
					{#if isSyncing}
						<span class="text-xs text-gray-400 shrink-0">&middot;</span>
						<Spinner className="size-3" />
					{/if}
					<span class="text-xs text-gray-400 shrink-0">
						&middot; {$i18n.t('{{count}} files in folder', { count: totalFileCount })}
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
								<GarbageBin className="size-3.5" />
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
							{@const fileStatus = file?.status ?? file?.data?.status}
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
												{#if fileStatus === 'uploading' || fileStatus === 'pending' || fileStatus === 'downloading' || fileStatus === 'parsing' || fileStatus === 'ingesting'}
													<Spinner className="size-3" />
												{:else if fileStatus === 'error' || fileStatus === 'cancelled'}
													<ExclamationTriangle className="size-3 text-red-500" />
												{:else}
													<DocumentPage className="size-3" />
												{/if}
											</div>
											<div class="line-clamp-1 text-xs">
												{file?.name ?? file?.meta?.name}
												{#if file?.meta?.size}
													<span class="text-gray-400">{formatFileSize(file?.meta?.size)}</span>
												{/if}
											</div>
										</div>
									</div>

									<div class="flex items-center gap-2 shrink-0 text-xs">
										{#if file?.added_at || file?.updated_at}
											<Tooltip
												content={dayjs((file.added_at ?? file.updated_at) * 1000).format('LLLL')}
											>
												<div>
													{dayjs((file.added_at ?? file.updated_at) * 1000).fromNow()}
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
		{@const fileStatus = file?.status ?? file?.data?.status}
		{@const looseKey = looseItem(file).key}
		{@const looseSel = (selection && isLooseSelectable(file) && $selectedStore?.has(looseKey)) ?? false}
		<!-- svelte-ignore a11y_no_static_element_interactions -->
		<div
			class="group flex cursor-pointer w-full px-1.5 py-0.5 bg-transparent dark:hover:bg-gray-850/50 hover:bg-white rounded-xl transition {selection
				? 'select-none'
				: ''} {looseSel
				? 'bg-blue-50 dark:bg-blue-900/20'
				: selectedFileId
					? ''
					: 'hover:bg-gray-100 dark:hover:bg-gray-850'}"
			on:pointerdown={() => onLoosePointerDown(file)}
			on:pointerenter={() => onLoosePointerEnter(file)}
		>
			{#if selection}
				<SelectCheckbox
					selectable={isLooseSelectable(file)}
					selected={looseSel}
					visible={!!$selectionModeStore}
					onToggle={() => selection.toggle(looseItem(file))}
				/>
			{/if}
			<button
				class="relative group flex items-center gap-1 rounded-xl p-2 text-left flex-1 justify-between"
				type="button"
				on:click={(e) => onLooseClick(file, e)}
			>
				<div>
					<div class="flex gap-2 items-center line-clamp-1">
						<div class="shrink-0">
							{#if fileStatus === 'uploading' || fileStatus === 'pending' || fileStatus === 'downloading' || fileStatus === 'parsing' || fileStatus === 'ingesting'}
								<Spinner className="size-3.5" />
							{:else if fileStatus === 'error' || fileStatus === 'cancelled'}
								<ExclamationTriangle className="size-3.5 text-red-500" />
							{:else}
								<DocumentPage className="size-3.5" />
							{/if}
						</div>

						<div class="line-clamp-1 text-sm">
							{file?.name ?? file?.meta?.name}
							{#if file?.meta?.size}
								<span class="text-xs text-gray-500">{formatFileSize(file?.meta?.size)}</span>
							{/if}
						</div>
					</div>
				</div>

				<div class="flex items-center gap-2 shrink-0">
					{#if file?.added_at || file?.updated_at}
						<Tooltip content={dayjs((file.added_at ?? file.updated_at) * 1000).format('LLLL')}>
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
										file?.user?.name ?? file?.user?.email ?? $i18n.t('Deleted User')
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
								const cloudSource =
									file?.meta?.source === 'onedrive' ||
									file?.meta?.source === 'google_drive' ||
									file?.meta?.source === 'confluence';
								if (cloudSource && file?.meta?.source_item_id) {
									// Cloud-provider loose file: remove via source removal
									onRemoveSource(file.meta.source_item_id, file?.name ?? file?.meta?.name);
								} else {
									// Local file: normal delete
									onDelete(file?.id ?? file?.tempId);
								}
							}}
						>
							<GarbageBin className="size-3.5" />
						</button>
					</Tooltip>
				</div>
			{/if}
		</div>
	{/each}
</div>
