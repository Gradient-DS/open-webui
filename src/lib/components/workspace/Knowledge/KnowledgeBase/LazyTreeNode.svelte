<script lang="ts">
	import dayjs from '$lib/dayjs';
	import duration from 'dayjs/plugin/duration';
	import relativeTime from 'dayjs/plugin/relativeTime';

	dayjs.extend(duration);
	dayjs.extend(relativeTime);

	import { getContext } from 'svelte';
	import { slide } from 'svelte/transition';
	import { quintOut } from 'svelte/easing';

	const i18n = getContext<any>('i18n');

	import { formatFileSize } from '$lib/utils';
	import { folderBadge, fileBadge, type TreeFolder, type TreeFile } from '../utils/treeStatus';

	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import DocumentPage from '$lib/components/icons/DocumentPage.svelte';
	import ExclamationTriangle from '$lib/components/icons/ExclamationTriangle.svelte';
	import Folder from '$lib/components/icons/Folder.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import ChevronRight from '$lib/components/icons/ChevronRight.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';

	export let node: TreeFolder;
	// True only for the top-level source nodes (which can be removed and show a
	// sync spinner). Synthesized subfolders deeper in the tree set this false.
	export let isSource: boolean = false;
	export let isSyncing: boolean = false;
	export let knowledge: any = null;

	// Shared state owned by LazyKnowledgeTree (reassigned there to drive reactivity).
	export let expanded: Record<string, boolean> = {};
	export let nodeCache: Record<string, any> = {};
	export let loadingPaths: Record<string, boolean> = {};

	export let toggle: (path: string) => void = () => {};
	export let loadMore: (path: string) => void = () => {};
	export let onClick: (file: TreeFile) => void = () => {};
	export let onRemoveSource: (itemId: string, name: string) => void = () => {};

	$: cache = nodeCache[node.path];
	$: badge = folderBadge(node.status_counts);
</script>

<div class="w-full">
	<!-- Folder header -->
	<div
		class="group flex items-center w-full px-1.5 py-0.5 hover:bg-gray-50 dark:hover:bg-gray-850/50 rounded-xl transition"
	>
		<button
			class="flex items-center gap-1.5 flex-1 {isSource
				? 'p-2 text-sm'
				: 'p-1.5 text-xs text-gray-500'} text-left"
			type="button"
			on:click={() => toggle(node.path)}
		>
			<div class="shrink-0 {isSource ? 'text-gray-500' : ''}">
				{#if loadingPaths[node.path]}
					<Spinner className="size-3" />
				{:else if expanded[node.path]}
					<ChevronDown className="size-3" strokeWidth="2.5" />
				{:else}
					<ChevronRight className="size-3" strokeWidth="2.5" />
				{/if}
			</div>
			<div class="shrink-0">
				<Folder className={isSource ? 'size-3.5' : 'size-3'} strokeWidth="2" />
			</div>
			<span class="line-clamp-1 font-medium">{node.name}</span>

			{#if isSource && isSyncing}
				<span class="text-xs text-gray-400 shrink-0">&middot;</span>
				<Spinner className="size-3" />
			{/if}

			<span class="text-xs text-gray-400 shrink-0">
				&middot; {$i18n.t('{{count}} files in folder', { count: node.child_count })}
			</span>

			{#if badge === 'failed'}
				<Tooltip content={$i18n.t('{{count}} failed', { count: node.status_counts?.failed ?? 0 })}>
					<ExclamationTriangle className="size-3 text-red-500 shrink-0" />
				</Tooltip>
			{:else if badge === 'pending'}
				<Spinner className="size-3" />
			{/if}
		</button>

		{#if isSource && knowledge?.write_access}
			<div class="flex items-center opacity-0 group-hover:opacity-100 transition-opacity">
				<Tooltip content={$i18n.t('Remove Source')}>
					<button
						class="p-1 rounded-full hover:bg-gray-100 dark:hover:bg-gray-850 transition"
						type="button"
						on:click={() => onRemoveSource(node.path, node.name)}
					>
						<XMark />
					</button>
				</Tooltip>
			</div>
		{/if}
	</div>

	<!-- Folder contents (lazily fetched on first expand) -->
	{#if expanded[node.path]}
		<div transition:slide={{ duration: 200, easing: quintOut, axis: 'y' }}>
			<div class="ml-3 pl-1 border-s border-gray-100 dark:border-gray-900">
				{#if cache?.loading && !cache?.folders?.length && !cache?.files?.length}
					<div class="flex items-center gap-2 px-1.5 py-1 text-xs text-gray-400">
						<Spinner className="size-3" />
					</div>
				{:else if cache}
					<!-- Child subfolders (recursive) -->
					{#each cache.folders as child (child.path)}
						<svelte:self
							node={child}
							isSource={false}
							{knowledge}
							{expanded}
							{nodeCache}
							{loadingPaths}
							{toggle}
							{loadMore}
							{onClick}
							{onRemoveSource}
						/>
					{/each}

					<!-- Direct files of this folder (no per-file delete inside folders) -->
					{#each cache.files as file (file.id)}
						{@const fb = fileBadge(file.status)}
						<div
							class="flex cursor-pointer w-full px-1.5 py-0.5 hover:bg-gray-50 dark:hover:bg-gray-850/50 rounded-xl transition"
						>
							<button
								class="relative flex items-center gap-1 rounded-xl p-1.5 text-left flex-1 justify-between text-gray-500"
								type="button"
								on:click={() => onClick(file)}
							>
								<div>
									<div class="flex gap-2 items-center line-clamp-1">
										<div class="shrink-0">
											{#if fb === 'spinner'}
												<Spinner className="size-3" />
											{:else if fb === 'error'}
												<Tooltip content={file.error || $i18n.t('Processing error')}>
													<ExclamationTriangle className="size-3 text-red-500" />
												</Tooltip>
											{:else}
												<DocumentPage className="size-3" />
											{/if}
										</div>
										<div class="line-clamp-1 text-xs">
											{file.name}
											{#if file.size}
												<span class="text-gray-400">{formatFileSize(file.size)}</span>
											{/if}
										</div>
									</div>
								</div>

								<div class="flex items-center gap-2 shrink-0 text-xs">
									{#if file.updated_at}
										<Tooltip content={dayjs(file.updated_at * 1000).format('LLLL')}>
											<div>{dayjs(file.updated_at * 1000).fromNow()}</div>
										</Tooltip>
									{/if}
								</div>
							</button>
						</div>
					{/each}

					{#if cache.hasMore}
						<button
							class="flex items-center gap-1.5 px-1.5 py-1 text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 transition"
							type="button"
							on:click={() => loadMore(node.path)}
						>
							{#if cache.loadingMore}
								<Spinner className="size-3" />
							{/if}
							{$i18n.t('Load more')}
						</button>
					{/if}
				{/if}
			</div>
		</div>
	{/if}
</div>
