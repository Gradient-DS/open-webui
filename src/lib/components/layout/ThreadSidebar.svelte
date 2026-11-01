<script lang="ts">
	import { getContext } from 'svelte';
	import type { Readable } from 'svelte/store';
	import type { i18n as I18n } from 'i18next';
	import { config } from '$lib/stores';
	import { threadAPI, type SidebarItem, type ThreadAPI } from '$lib/apis/threads';
	import ThreadState from '$lib/components/common/ThreadState.svelte';
	import { threadCacheOrigin, watchThreadVisibility } from '$lib/utils/threadCache';
	import {
		filterSidebar,
		ThreadSidebar,
		conversationId,
		type SidebarFilter,
		type SidebarSnapshot
	} from '$lib/utils/threads';

	let {
		token,
		contextEpoch,
		api,
		filter = { archived: false },
		onselect
	}: {
		token: string;
		contextEpoch: string;
		api?: ThreadAPI;
		filter?: SidebarFilter;
		onselect: (item: SidebarItem) => void;
	} = $props();
	const i18n = getContext<Readable<I18n>>('i18n');
	let query = $state('');
	let snapshot = $state<SidebarSnapshot | null>(null);
	let controller: ThreadSidebar | null = null;
	let boundContext = $state('');
	let boundToken = $state('');
	let boundQuery = $state('');
	const visible = $derived(filterSidebar(snapshot?.items ?? [], filter));

	$effect(() => {
		const enabled = $config?.features?.enable_v2_threads;
		const credential = token;
		const context = contextEpoch;
		const search = query;
		const transport = api ?? threadAPI(credential);
		boundContext = context;
		boundToken = credential;
		boundQuery = search;
		if (!enabled || !credential || !context) {
			snapshot = null;
			return;
		}
		const origin = threadCacheOrigin();
		const next = new ThreadSidebar(
			transport,
			(value) => {
				snapshot = value;
			},
			() => void origin.clear()
		);
		controller = next;
		const current = origin.stamp();
		const unwatch = watchThreadVisibility(
			(state) => next.clear(state),
			() => void (current() ? next.refresh() : next.search(search))
		);
		void next.search(search);
		return () => {
			unwatch();
			next.clear();
			controller = null;
		};
	});
</script>

{#if $config?.features?.enable_v2_threads}
	<input
		type="search"
		bind:value={query}
		aria-label={$i18n.t('Search Chats')}
		placeholder={$i18n.t('Search Chats')}
	/>
	{#if boundContext === contextEpoch && boundToken === token && boundQuery === query}
		{#if snapshot?.state}
			<ThreadState state={snapshot.state} retry={() => controller?.search(query)} />
		{:else}
			<ul>
				{#each visible as item (conversationId(item))}
					<li data-folder-id={item.organization?.folder_id}>
						<button onclick={() => onselect(item)}>
							{#if item.organization?.pinned}<span aria-label={$i18n.t('Pinned')}>★</span>{/if}
							{item.title ?? $i18n.t('New Chat')}
							{#if (item.organization?.last_read_at ?? 0) * 1000 < Date.parse(item.updated_at)}
								<span aria-label={$i18n.t('Unread')}>•</span>
							{/if}
						</button>
					</li>
				{/each}
			</ul>
			{#if snapshot?.loading}<p role="status">{$i18n.t('Loading chats')}</p>
			{:else if visible.length === 0}<p>{$i18n.t('No chats found')}</p>{/if}
			{#if snapshot?.cursor}
				<button disabled={snapshot.loading} onclick={() => controller?.more()}
					>{$i18n.t('Load more chats')}</button
				>
			{/if}
		{/if}
	{/if}
{/if}
