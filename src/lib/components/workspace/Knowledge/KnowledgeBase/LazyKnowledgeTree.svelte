<script lang="ts">
	import dayjs from '$lib/dayjs';
	import duration from 'dayjs/plugin/duration';
	import relativeTime from 'dayjs/plugin/relativeTime';

	dayjs.extend(duration);
	dayjs.extend(relativeTime);

	import { getContext } from 'svelte';

	const i18n = getContext<any>('i18n');

	import { formatFileSize } from '$lib/utils';
	import { getKnowledgeTree } from '$lib/apis/knowledge';
	import { fileBadge, type TreeFolder, type TreeFile, type TreeLevel } from '../utils/treeStatus';

	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import DocumentPage from '$lib/components/icons/DocumentPage.svelte';
	import ExclamationTriangle from '$lib/components/icons/ExclamationTriangle.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import LazyTreeNode from './LazyTreeNode.svelte';

	export let knowledge: any = null;
	export let selectedFileId: string | null = null;
	export let isSyncing: boolean = false;
	// Parent bumps this counter after any mutation (delete / remove-source /
	// sync completion / content edit) so the tree re-fetches what's open.
	export let refreshSignal: number = 0;

	export let onClick: (file: TreeFile) => void = () => {};
	export let onDelete: (fileId: string) => void = () => {};
	export let onRemoveSource: (itemId: string, name: string) => void = () => {};

	const PAGE_LIMIT = 100;

	// Shared state passed down into the recursive nodes. Reassigned wholesale on
	// every change so Svelte re-renders the affected subtree.
	let expanded: Record<string, boolean> = {};
	let nodeCache: Record<string, any> = {};

	let loadingRoot = true;
	let rootError = false;
	let rootFolders: TreeFolder[] = [];
	let rootFiles: TreeFile[] = [];
	let rootCursor: string | null = null;
	let rootHasMore = false;
	let loadingMoreRoot = false;

	const fetchLevel = async (path: string, cursor: string | null): Promise<TreeLevel | null> => {
		if (!knowledge?.id) return null;
		return await getKnowledgeTree(localStorage.token, knowledge.id, path, cursor, PAGE_LIMIT).catch(
			() => null
		);
	};

	const loadRoot = async () => {
		loadingRoot = true;
		rootError = false;
		const res = await fetchLevel('', null);
		if (res) {
			rootFolders = res.folders ?? [];
			rootFiles = res.files ?? [];
			rootCursor = res.next_cursor ?? null;
			rootHasMore = res.has_more ?? false;
		} else {
			rootError = true;
		}
		loadingRoot = false;
	};

	const loadNode = async (path: string) => {
		nodeCache[path] = { ...(nodeCache[path] ?? {}), loading: true };
		nodeCache = nodeCache;
		const res = await fetchLevel(path, null);
		nodeCache[path] = {
			folders: res?.folders ?? [],
			files: res?.files ?? [],
			cursor: res?.next_cursor ?? null,
			hasMore: res?.has_more ?? false,
			loading: false,
			loadingMore: false
		};
		nodeCache = nodeCache;
	};

	const toggle = (path: string) => {
		expanded[path] = !expanded[path];
		expanded = expanded;
		if (expanded[path] && !nodeCache[path]) {
			loadNode(path);
		}
	};

	const loadMore = async (path: string) => {
		const cache = nodeCache[path];
		if (!cache?.hasMore || cache.loadingMore) return;
		cache.loadingMore = true;
		nodeCache = nodeCache;
		const res = await fetchLevel(path, cache.cursor);
		nodeCache[path] = {
			...cache,
			files: [...cache.files, ...(res?.files ?? [])],
			cursor: res?.next_cursor ?? null,
			hasMore: res?.has_more ?? false,
			loadingMore: false
		};
		nodeCache = nodeCache;
	};

	const loadMoreRoot = async () => {
		if (!rootHasMore || loadingMoreRoot) return;
		loadingMoreRoot = true;
		const res = await fetchLevel('', rootCursor);
		rootFiles = [...rootFiles, ...(res?.files ?? [])];
		rootCursor = res?.next_cursor ?? null;
		rootHasMore = res?.has_more ?? false;
		loadingMoreRoot = false;
	};

	// Refresh: keep the user's expansion, drop cached contents, reload root and
	// re-fetch every open folder. Runs on mount (refreshSignal starts at 0,
	// lastRefresh undefined) and on every parent bump.
	let lastRefresh: number | undefined;
	const refresh = async () => {
		const reopen = Object.keys(expanded).filter((p) => expanded[p]);
		nodeCache = {};
		await loadRoot();
		await Promise.all(reopen.map((p) => loadNode(p)));
	};
	$: if (refreshSignal !== lastRefresh) {
		lastRefresh = refreshSignal;
		refresh();
	}
</script>

<div class="max-h-full flex flex-col w-full gap-[0.5px]">
	{#if loadingRoot && !rootFolders.length && !rootFiles.length}
		<div class="flex items-center justify-center gap-2 py-6 text-xs text-gray-400">
			<Spinner className="size-4" />
		</div>
	{:else if rootError}
		<div class="py-6 text-center text-xs text-gray-500">
			{$i18n.t('Failed to load files.')}
		</div>
	{:else}
		<!-- Source folders -->
		{#each rootFolders as source (source.path)}
			<LazyTreeNode
				node={source}
				isSource={true}
				{isSyncing}
				{knowledge}
				{expanded}
				{nodeCache}
				{toggle}
				{loadMore}
				{onClick}
				{onRemoveSource}
			/>
		{/each}

		<!-- Root loose files (local uploads) -->
		{#each rootFiles as file (file.id)}
			{@const fb = fileBadge(file.status)}
			<div
				class="flex cursor-pointer w-full px-1.5 py-0.5 bg-transparent dark:hover:bg-gray-850/50 hover:bg-white rounded-xl transition {selectedFileId
					? ''
					: 'hover:bg-gray-100 dark:hover:bg-gray-850'}"
			>
				<button
					class="relative group flex items-center gap-1 rounded-xl p-2 text-left flex-1 justify-between"
					type="button"
					on:click={() => onClick(file)}
				>
					<div>
						<div class="flex gap-2 items-center line-clamp-1">
							<div class="shrink-0">
								{#if fb === 'spinner'}
									<Spinner className="size-3.5" />
								{:else if fb === 'error'}
									<ExclamationTriangle className="size-3.5 text-red-500" />
								{:else}
									<DocumentPage className="size-3.5" />
								{/if}
							</div>
							<div class="line-clamp-1 text-sm">
								{file.name}
								{#if file.size}
									<span class="text-xs text-gray-500">{formatFileSize(file.size)}</span>
								{/if}
							</div>
						</div>
					</div>

					<div class="flex items-center gap-2 shrink-0">
						{#if file.updated_at}
							<Tooltip content={dayjs(file.updated_at * 1000).format('LLLL')}>
								<div>{dayjs(file.updated_at * 1000).fromNow()}</div>
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
								on:click={() => onDelete(file.id)}
							>
								<XMark />
							</button>
						</Tooltip>
					</div>
				{/if}
			</div>
		{/each}

		{#if rootHasMore}
			<button
				class="flex items-center gap-1.5 px-1.5 py-1 text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 transition"
				type="button"
				on:click={loadMoreRoot}
			>
				{#if loadingMoreRoot}
					<Spinner className="size-3" />
				{/if}
				{$i18n.t('Load more')}
			</button>
		{/if}

		{#if !rootFolders.length && !rootFiles.length}
			<div class="my-3 flex flex-col justify-center text-center text-gray-500 text-xs">
				<div>{$i18n.t('No content found')}</div>
			</div>
		{/if}
	{/if}
</div>
