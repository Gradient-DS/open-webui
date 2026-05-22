<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import { streamOnboarding, type OnboardingMessage } from '$lib/apis/onboarding';

	const i18n = getContext('i18n');

	// Fired with the AssistantDraft once the interview completes.
	export let onComplete: (draft: any) => void;
	// Fired if the onboarding agent is unreachable.
	export let onUnavailable: () => void;

	const chatId = `onb-${crypto.randomUUID()}`;
	let transcript: OnboardingMessage[] = [];
	let input = '';
	let streaming = false;
	let liveAnswer = '';

	const runTurn = async () => {
		streaming = true;
		liveAnswer = '';
		try {
			for await (const event of streamOnboarding(localStorage.token, chatId, transcript)) {
				if (event.type === 'content') {
					liveAnswer += event.text;
				} else if (event.type === 'draft') {
					onComplete(event.draft);
					return;
				}
			}
			if (liveAnswer.trim() !== '') {
				transcript = [...transcript, { role: 'assistant', content: liveAnswer }];
			}
		} catch (e) {
			console.error('Onboarding agent error:', e);
			onUnavailable();
		} finally {
			streaming = false;
			liveAnswer = '';
		}
	};

	const sendHandler = async () => {
		if (input.trim() === '' || streaming) return;
		transcript = [...transcript, { role: 'user', content: input.trim() }];
		input = '';
		await runTurn();
	};

	onMount(() => {
		// Open with a fixed prompt so the first turn has user content.
		transcript = [
			{
				role: 'user',
				content: $i18n.t('I want to create an assistant. Please help me design it.')
			}
		];
		runTurn();
	});
</script>

<div class="flex flex-col gap-3 max-w-3xl mx-auto w-full p-1">
	<div class="text-lg font-medium">{$i18n.t('Build your assistant')}</div>
	<div class="text-xs text-gray-400">
		{$i18n.t('Answer a few questions and the assistant will be drafted for you.')}
	</div>

	<div class="flex flex-col gap-3 min-h-[12rem]">
		{#each transcript.slice(1) as msg}
			<div class="text-sm {msg.role === 'user' ? 'text-right' : ''}">
				<span
					class="inline-block rounded-lg px-3 py-2 {msg.role === 'user'
						? 'bg-gray-100 dark:bg-gray-800'
						: 'bg-gray-50 dark:bg-gray-850'}"
				>
					{msg.content}
				</span>
			</div>
		{/each}
		{#if streaming && liveAnswer}
			<div class="text-sm">
				<span class="inline-block rounded-lg px-3 py-2 bg-gray-50 dark:bg-gray-850">
					{liveAnswer}
				</span>
			</div>
		{/if}
		{#if streaming && !liveAnswer}
			<div class="text-sm text-gray-400">{$i18n.t('Thinking...')}</div>
		{/if}
	</div>

	<div class="flex gap-2">
		<input
			class="flex-1 rounded-lg px-3 py-2 text-sm bg-gray-50 dark:bg-gray-850 outline-hidden"
			bind:value={input}
			disabled={streaming}
			placeholder={$i18n.t('Type your answer...')}
			on:keydown={(e) => e.key === 'Enter' && sendHandler()}
		/>
		<button
			class="px-4 py-2 text-sm rounded-lg bg-black text-white dark:bg-white dark:text-black disabled:opacity-50"
			disabled={streaming || input.trim() === ''}
			on:click={sendHandler}
		>
			{$i18n.t('Send')}
		</button>
	</div>
</div>
