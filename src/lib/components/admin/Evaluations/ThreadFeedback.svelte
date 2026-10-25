<script lang="ts">
	import { getContext } from 'svelte';
	import type { Readable } from 'svelte/store';
	import type { i18n as I18n } from 'i18next';
	import {
		readThreadFeedback,
		type FeedbackItem,
		type FeedbackCount
	} from '$lib/utils/threadFeedback';

	let { token, summary = false }: { token: string; summary?: boolean } = $props();
	const i18n = getContext<Readable<I18n>>('i18n');
	let rows = $state<(FeedbackItem | FeedbackCount)[]>([]);
	let cursor = $state<string | null>(null);
	let loading = $state(false);
	let failed = $state(false);
	let filter = $state('');
	let threadId = $state('');
	let retry = $state(0);
	let requestPage = $state('');
	let binding = '';

	$effect(() => {
		const key = `${token}\0${summary}\0${threadId}\0${retry}`;
		if (binding !== key) {
			binding = key;
			requestPage = '';
			rows = [];
			cursor = null;
		}
		const controller = new AbortController();
		let current = true;
		const options = { summary, threadId: summary ? '' : threadId, cursor: requestPage };
		if (!requestPage) rows = [];
		loading = true;
		failed = false;
		readThreadFeedback(token, options, controller.signal)
			.then((page) => {
				if (!current) return;
				rows = options.cursor ? [...rows, ...page.items] : page.items;
				cursor = page.next_cursor;
			})
			.catch(() => {
				if (current) {
					rows = [];
					failed = true;
				}
			})
			.finally(() => {
				if (current) loading = false;
			});
		return () => {
			current = false;
			controller.abort();
		};
	});
</script>

<div class="space-y-4 p-4">
	{#if !summary}
		<form
			onsubmit={(event) => {
				event.preventDefault();
				requestPage = '';
				threadId = filter.trim();
			}}
		>
			<label class="text-sm"
				>{$i18n.t('Thread id')}
				<input class="border rounded p-1 mx-2" bind:value={filter} />
			</label>
			<button type="submit">{$i18n.t('Search')}</button>
		</form>
	{/if}
	{#if failed}
		<p role="status">{$i18n.t('This chat is unavailable')}</p>
		<button
			onclick={() => {
				requestPage = '';
				retry += 1;
			}}>{$i18n.t('Retry')}</button
		>
	{:else}
		<table class="w-full text-sm text-left">
			<thead
				><tr>
					<th>{$i18n.t('Model')}</th><th>{$i18n.t('Agent')}</th><th>{$i18n.t('Rating')}</th>
					{#if summary}<th>{$i18n.t('Rating count')}</th>
					{:else}<th>{$i18n.t('Time')}</th><th>{$i18n.t('Thread id')}</th><th
							>{$i18n.t('Comment')}</th
						>{/if}
				</tr></thead
			>
			<tbody>
				{#each rows as row}
					<tr
						><td>{row.model}</td><td>{row.agent}</td><td>{row.rating}</td>
						{#if 'count' in row}<td>{row.count}</td>
						{:else}<td>{row.time}</td><td>{row.thread_id}</td><td>{row.comment ?? ''}</td>{/if}
					</tr>
				{/each}
			</tbody>
		</table>
		{#if loading}<p role="status">{$i18n.t('Loading...')}</p>
		{:else if rows.length === 0}<p>{$i18n.t('No feedback found')}</p>{/if}
		{#if cursor && !loading}<button
				onclick={() => {
					requestPage = cursor ?? '';
				}}>{$i18n.t('Load more')}</button
			>{/if}
	{/if}
</div>
