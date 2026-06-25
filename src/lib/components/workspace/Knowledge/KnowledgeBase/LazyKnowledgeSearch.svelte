<script lang="ts">
	import dayjs from '$lib/dayjs';
	import duration from 'dayjs/plugin/duration';
	import relativeTime from 'dayjs/plugin/relativeTime';

	dayjs.extend(duration);
	dayjs.extend(relativeTime);

	import { getContext } from 'svelte';

	const i18n = getContext<any>('i18n');

	import { formatFileSize } from '$lib/utils';
	import { searchKnowledgeTree } from '$lib/apis/knowledge';
	import {
		fileBadge,
		breadcrumbSegments,
		type SearchHit,
		type SearchLevel
	} from '../utils/treeStatus';

	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import DocumentPage from '$lib/components/icons/DocumentPage.svelte';
	import ExclamationTriangle from '$lib/components/icons/ExclamationTriangle.svelte';
	import Folder from '$lib/components/icons/Folder.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';

	export let knowledge: any = null;
	export let query: string = '';
	export let selectedFileId: string | null = null;
	export let onClick: (file: SearchHit) => void = () => {};

	const PAGE_LIMIT = 100;
	const DEBOUNCE_MS = 300;

	let items: SearchHit[] = [];
	let cursor: string | null = null;
	let hasMore = false;
	let loading = false;
	let loadingMore = false;
	let errored = false;

	let debounceTimer: ReturnType<typeof setTimeout>;
	// Guards against out-of-order responses when the user keeps typing.
	let runId = 0;
	// The query the currently-shown results belong to (so "Load more" paginates
	// the right search even if the box has since changed mid-debounce).
	let activeQuery = '';

	const fetchPage = async (q: string, cur: string | null): Promise<SearchLevel | null> => {
		if (!knowledge?.id) return null;
		return await searchKnowledgeTree(localStorage.token, knowledge.id, q, cur, PAGE_LIMIT).catch(
			() => null
		);
	};

	const runSearch = async (q: string) => {
		const myRun = ++runId;
		activeQuery = q;
		loading = true;
		errored = false;
		const res = await fetchPage(q, null);
		if (myRun !== runId) return; // a newer search superseded this one
		if (res) {
			items = res.items ?? [];
			cursor = res.next_cursor ?? null;
			hasMore = res.has_more ?? false;
		} else {
			errored = true;
			items = [];
		}
		loading = false;
	};

	const loadMore = async () => {
		if (!hasMore || loadingMore) return;
		const myRun = runId;
		loadingMore = true;
		const res = await fetchPage(activeQuery, cursor);
		if (myRun !== runId) return;
		if (res) {
			items = [...items, ...(res.items ?? [])];
			cursor = res.next_cursor ?? null;
			hasMore = res.has_more ?? false;
		}
		loadingMore = false;
	};

	// Debounced re-search whenever the query changes (and on mount). Passing the
	// query keeps the reactive dependency explicit (no bare-expression statement).
	const scheduleSearch = (q: string) => {
		clearTimeout(debounceTimer);
		debounceTimer = setTimeout(() => runSearch(q), DEBOUNCE_MS);
	};
	$: scheduleSearch(query);
</script>

<div class="max-h-full flex flex-col w-full gap-[0.5px]">
	{#if loading && !items.length}
		<div class="flex items-center justify-center gap-2 py-6 text-xs text-gray-400">
			<Spinner className="size-4" />
		</div>
	{:else if errored}
		<div class="py-6 text-center text-xs text-gray-500">{$i18n.t('Failed to load files.')}</div>
	{:else if !items.length}
		<div class="my-3 flex flex-col justify-center text-center text-gray-500 text-xs">
			<div>{$i18n.t('No results found')}</div>
		</div>
	{:else}
		{#each items as file (file.id)}
			{@const fb = fileBadge(file.status)}
			{@const crumbs = breadcrumbSegments(file.relative_path)}
			<div
				class="flex cursor-pointer w-full px-1.5 py-0.5 hover:bg-gray-50 dark:hover:bg-gray-850/50 rounded-xl transition {selectedFileId ===
				file.id
					? 'bg-gray-50 dark:bg-gray-850/50'
					: ''}"
			>
				<button
					class="relative flex items-center gap-1 rounded-xl p-2 text-left flex-1 justify-between"
					type="button"
					on:click={() => onClick(file)}
				>
					<div class="min-w-0">
						<div class="flex gap-2 items-center line-clamp-1">
							<div class="shrink-0">
								{#if fb === 'spinner'}
									<Spinner className="size-3.5" />
								{:else if fb === 'error'}
									<Tooltip content={file.error || $i18n.t('Processing error')}>
										<ExclamationTriangle className="size-3.5 text-red-500" />
									</Tooltip>
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
						{#if crumbs.length}
							<div class="flex items-center gap-1 text-xs text-gray-400 mt-0.5 ml-5 line-clamp-1">
								<Folder className="size-3 shrink-0" strokeWidth="2" />
								<span class="line-clamp-1">{crumbs.join(' / ')}</span>
							</div>
						{/if}
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

		{#if hasMore}
			<button
				class="flex items-center gap-1.5 px-1.5 py-1 text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 transition"
				type="button"
				on:click={loadMore}
			>
				{#if loadingMore}
					<Spinner className="size-3" />
				{/if}
				{$i18n.t('Load more')}
			</button>
		{/if}
	{/if}
</div>
