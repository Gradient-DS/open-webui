<script lang="ts">
	import { getContext, tick } from 'svelte';
	import type { Readable } from 'svelte/store';
	import type { i18n as I18n } from 'i18next';
	import { config, user } from '$lib/stores';
	import { threadAPI, type MessageView, type ThreadAPI } from '$lib/apis/threads';
	import ThreadState from '$lib/components/common/ThreadState.svelte';
	import { ThreadWindow, type VersionTarget, type WindowSnapshot } from '$lib/utils/threads';
	import { browserThreadCache, watchThreadVisibility } from '$lib/utils/threadCache';
	import Message from './Messages/Message.svelte';

	let {
		threadId,
		token,
		api,
		contextEpoch,
		parent
	}: {
		threadId: string;
		token: string;
		api?: ThreadAPI;
		contextEpoch: string;
		parent?: VersionTarget;
	} = $props();
	const i18n = getContext<Readable<I18n>>('i18n');
	let snapshot = $state<WindowSnapshot | null>(null);
	let controller: ThreadWindow | null = null;
	let retry = () => {};
	let container = $state<HTMLDivElement>();
	const family = $derived(snapshot?.family);
	let boundContext = $state('');
	let boundThread = $state('');
	let boundToken = $state('');
	let activeThread = $state('');
	let activeParent = $state<VersionTarget>();
	const noop = () => {};
	const openThread = async (next: ThreadWindow, id: string, ancestor?: VersionTarget) => {
		await next.open(id, ancestor);
		await tick();
		if (controller === next && container && next.snapshot.page) {
			container.scrollTop = container.scrollHeight;
		}
	};

	$effect(() => {
		const enabled = $config?.features?.enable_v2_threads;
		const credential = token;
		const context = contextEpoch;
		const id = threadId;
		const transport = api ?? threadAPI(credential);
		const ancestor = parent;
		boundContext = context;
		boundThread = id;
		boundToken = credential;
		activeThread = id;
		activeParent = ancestor;
		if (!enabled || !credential || !id || !context) {
			snapshot = null;
			return;
		}
		let cache = browserThreadCache(transport, context);
		let next = new ThreadWindow(
			transport,
			(value) => {
				snapshot = value;
			},
			cache
		);
		controller = next;
		const reopen = () => {
			cache.dispose();
			next.clear();
			cache = browserThreadCache(transport, context);
			next = new ThreadWindow(transport, (value) => (snapshot = value), cache);
			controller = next;
			void openThread(next, activeThread, activeParent);
		};
		retry = reopen;
		const unwatch = watchThreadVisibility(
			(state) => next.clear(state),
			() => next.resume(reopen)
		);
		void openThread(next, id, ancestor);
		return () => {
			unwatch();
			cache.dispose();
			next.clear();
			controller = null;
		};
	});

	export const projectedMessage = async (
		eventContext: string,
		id: string,
		generation: string,
		message: MessageView
	) => {
		if (eventContext !== contextEpoch || eventContext !== boundContext) return;
		const follow =
			container && container.scrollHeight - container.scrollTop - container.clientHeight < 64;
		controller?.stream(id, generation, message);
		await tick();
		if (follow && container) container.scrollTop = container.scrollHeight;
	};

	const older = async () => {
		if (!container || !controller || snapshot?.loading) return;
		const height = container.scrollHeight;
		const top = container.scrollTop;
		const current = controller;
		await current.older();
		await tick();
		if (controller === current) container.scrollTop = top + container.scrollHeight - height;
	};
	const selectVersion = async (targets: VersionTarget[], index: number) => {
		const target = targets[index];
		if (!target || target.threadId === activeThread) return;
		activeThread = target.threadId;
		activeParent = targets[0];
		if (controller) await openThread(controller, target.threadId, targets[0]);
	};
</script>

{#snippet arrows(targets: VersionTarget[])}
	{@const index = targets.findIndex((target) => target.threadId === activeThread)}
	<div class="flex items-center gap-2 text-sm">
		<button
			aria-label={$i18n.t('Previous version')}
			disabled={index <= 0}
			onclick={() => selectVersion(targets, index - 1)}>‹</button
		>
		<span
			>{$i18n.t('Version {{current}} of {{total}}', {
				current: index + 1,
				total: targets.length
			})}</span
		>
		<button
			aria-label={$i18n.t('Next version')}
			disabled={index >= targets.length - 1}
			onclick={() => selectVersion(targets, index + 1)}>›</button
		>
	</div>
{/snippet}

{#if $config?.features?.enable_v2_threads && boundContext === contextEpoch && boundThread === threadId && boundToken === token && snapshot}
	<div
		bind:this={container}
		class="h-full overflow-y-auto"
		role="region"
		aria-label={$i18n.t('Conversation')}
		onscroll={() => {
			if (container && container.scrollTop < 64) void older();
		}}
	>
		{#if snapshot.state}
			<ThreadState state={snapshot.state} retry={() => retry()} />
		{:else if snapshot.page}
			{#if snapshot.page.next_cursor}
				<button disabled={snapshot.loading} onclick={older}>{$i18n.t('Load older messages')}</button
				>
			{/if}
			{#if (snapshot.page.preferences as { fallback?: boolean } | null)?.fallback}
				<p role="note">{$i18n.t('Older chat, only for you')}</p>
			{/if}
			{#if family && !Object.values(snapshot.history.messages).some( (message) => message.positions.includes(family![0].at) )}
				{@render arrows(family)}
			{/if}
			<div role="list">
				{#each Object.values(snapshot.history.messages) as message, idx (message.id)}
					<Message
						history={snapshot.history}
						messageId={message.id}
						chatId={activeThread}
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
						regenerateResponse={noop}
						continueResponse={noop}
						mergeResponses={noop}
						addMessages={noop}
						triggerScroll={noop}
					/>
					{#if family && message.positions.includes(family[0].at)}
						{@render arrows(family)}
					{:else if snapshot.history.versions[message.position]}
						{@render arrows(snapshot.history.versions[message.position])}
					{/if}
				{/each}
			</div>
		{:else if snapshot.loading}
			<p role="status">{$i18n.t('Loading chat')}</p>
		{/if}
	</div>
{/if}
