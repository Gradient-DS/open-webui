<script lang="ts">
	import { getContext } from 'svelte';
	import type { Readable } from 'svelte/store';
	import type { i18n as I18n } from 'i18next';
	import { goto } from '$app/navigation';
	import { user, WEBUI_NAME } from '$lib/stores';
	import { ThreadError, type ProjectionPage } from '$lib/apis/threads';
	import { buildThreadHistory, mergeProjection, type ThreadHistory } from '$lib/utils/threads';
	import { sharingAPI, shareStateText } from '$lib/utils/threadSharing';
	import Message from './Messages/Message.svelte';

	let { shareId }: { shareId: string } = $props();
	const i18n = getContext<Readable<I18n>>('i18n');
	let page = $state<ProjectionPage | null>(null);
	let history = $state<ThreadHistory>({ messages: {}, currentId: null, versions: {} });
	let error = $state('');
	let busy = $state(false);
	let older = $state(false);
	let boundId = $state('');
	let boundUser = $state('');
	const noop = () => {};
	const clear = () => {
		page = null;
		history = { messages: {}, currentId: null, versions: {} };
	};

	$effect(() => {
		const id = shareId;
		const identity = $user?.id ?? '';
		const token = localStorage.token ?? '';
		boundId = id;
		boundUser = identity;
		clear();
		error = '';
		older = false;
		let active = true;
		let pending = false;
		const api = sharingAPI(token);
		if (!identity || !token) {
			void goto(`/auth?redirect=${encodeURIComponent(`/s/${id}`)}`);
			return;
		}
		const refresh = async () => {
			if (pending) return;
			pending = true;
			try {
				let next = await api.read(id);
				// Each refresh obtains fresh authority for every displayed page.
				// Never retain the old window across a refusal or a new generation.
				const seen = new Set<string>();
				while (older && next.next_cursor) {
					if (seen.has(next.next_cursor)) throw new ThreadError('reconnect');
					seen.add(next.next_cursor);
					next = mergeProjection(next, await api.read(id, next.next_cursor));
				}
				if (!active) return;
				const nextHistory = buildThreadHistory(next, []);
				page = next;
				history = nextHistory;
				error = '';
			} catch (e) {
				if (!active) return;
				clear();
				error = shareStateText(e);
			} finally {
				pending = false;
			}
		};
		void refresh();
		const timer = setInterval(refresh, 3000);
		const revalidate = () => {
			clear();
			void refresh();
		};
		const offline = () => {
			clear();
			error = shareStateText(new ThreadError('reconnect'));
		};
		window.addEventListener('offline', offline);
		window.addEventListener('online', revalidate);
		window.addEventListener('focus', revalidate);
		return () => {
			active = false;
			clearInterval(timer);
			window.removeEventListener('online', revalidate);
			window.removeEventListener('offline', offline);
			window.removeEventListener('focus', revalidate);
		};
	});

	const clone = async () => {
		busy = true;
		try {
			const result = await sharingAPI(localStorage.token ?? '').clone(shareId);
			await goto(`/c/${result.id}`);
		} catch (e) {
			clear();
			error = shareStateText(e);
		} finally {
			busy = false;
		}
	};
</script>

<svelte:head
	><title
		>{boundId === shareId && boundUser === ($user?.id ?? '') && page?.title
			? `${page.title} / ${$WEBUI_NAME}`
			: $WEBUI_NAME}</title
	></svelte:head
>

<div class="h-screen overflow-auto p-5">
	{#if boundId === shareId && boundUser === ($user?.id ?? '')}
		{#if error}<p role="alert">{$i18n.t(error)}</p>{/if}
		{#if page}
			<h1 class="text-2xl">{page.title ?? ''}</h1>
			{#if page.next_cursor}<button onclick={() => (older = true)}
					>{$i18n.t('Load older messages')}</button
				>{/if}
			{#each Object.values(history.messages) as message, idx (message.id)}
				<Message
					{history}
					messageId={message.id}
					chatId={page.thread_id}
					{idx}
					user={$user}
					selectedModels={[]}
					readOnly
					allowDelete={false}
					editCodeBlock={false}
					gotoMessage={noop}
					showPreviousMessage={noop}
					showNextMessage={noop}
					updateChat={noop}
					editMessage={noop}
					saveMessage={noop}
					deleteMessage={noop}
					rateMessage={noop}
					actionMessage={noop}
					submitMessage={noop}
					continueResponse={noop}
					regenerateResponse={noop}
					mergeResponses={noop}
					addMessages={noop}
					triggerScroll={noop}
				/>
			{/each}
			{#if $user?.role === 'admin' || ($user?.permissions?.chat?.import ?? true)}
				<button class="rounded border p-2" disabled={busy} onclick={clone}
					>{$i18n.t('Continue in my own chat')}</button
				>
			{/if}
		{/if}
	{/if}
</div>
