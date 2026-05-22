<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import { user } from '$lib/stores';
	import { streamOnboarding, type OnboardingMessage } from '$lib/apis/onboarding';
	import Messages from '$lib/components/chat/Messages.svelte';
	import Knowledge from '$lib/components/workspace/Models/Knowledge.svelte';

	const i18n = getContext('i18n');

	// Fired with the AssistantDraft once the interview completes.
	export let onComplete: (draft: any) => void;
	// Fired if the onboarding agent is unreachable.
	export let onUnavailable: () => void;
	// Knowledge (KBs / uploaded files) the user attaches during the
	// interview — carried straight through to the drafted assistant.
	export let knowledge: any[] = [];

	const chatId = `onb-${crypto.randomUUID()}`;

	// agentTranscript = what we send to the agent (includes the hidden seed
	// turn). history = the on-screen chat tree rendered by Messages.svelte.
	let agentTranscript: OnboardingMessage[] = [];
	let history: { messages: Record<string, any>; currentId: string | null } = {
		messages: {},
		currentId: null
	};
	let prompt = '';
	let input = '';
	let streaming = false;

	const now = () => Math.floor(Date.now() / 1000);

	/** Append a message node to the history tree; returns its id. */
	const appendMessage = (role: 'user' | 'assistant', content: string): string => {
		const id = crypto.randomUUID();
		const parentId = history.currentId;
		history.messages[id] = {
			id,
			parentId,
			childrenIds: [],
			role,
			content,
			timestamp: now(),
			...(role === 'assistant'
				? { model: '', modelName: $i18n.t('Assistant Builder'), modelIdx: 0, done: false }
				: {})
		};
		if (parentId && history.messages[parentId]) {
			history.messages[parentId].childrenIds = [
				...history.messages[parentId].childrenIds,
				id
			];
		}
		history.currentId = id;
		history = history; // trigger Svelte reactivity
		return id;
	};

	const runTurn = async () => {
		streaming = true;
		const assistantId = appendMessage('assistant', '');
		try {
			let answer = '';
			for await (const event of streamOnboarding(localStorage.token, chatId, agentTranscript)) {
				if (event.type === 'content') {
					answer += event.text;
					history.messages[assistantId].content = answer;
					history = history;
				} else if (event.type === 'draft') {
					onComplete(event.draft);
					return;
				}
			}
			history.messages[assistantId].content = answer;
			history.messages[assistantId].done = true;
			history = history;
			if (answer.trim() !== '') {
				agentTranscript = [...agentTranscript, { role: 'assistant', content: answer }];
			}
		} catch (e) {
			console.error('Onboarding agent error:', e);
			onUnavailable();
		} finally {
			streaming = false;
		}
	};

	const sendHandler = async () => {
		if (input.trim() === '' || streaming) return;
		const text = input.trim();
		input = '';
		agentTranscript = [...agentTranscript, { role: 'user', content: text }];
		appendMessage('user', text);
		await runTurn();
	};

	onMount(() => {
		// Seed turn — sent to the agent but not shown on screen, so the
		// agent opens the conversation with its first question.
		agentTranscript = [
			{
				role: 'user',
				content: $i18n.t('I want to create an assistant. Please help me design it.')
			}
		];
		runTurn();
	});
</script>

<div class="onboarding-chat flex flex-col h-full w-full overflow-auto">
	<div class="m-auto w-full max-w-3xl px-3 py-8 flex flex-col gap-4">
		<div>
			<div class="text-lg font-medium">{$i18n.t('Build your assistant')}</div>
			<div class="text-xs text-gray-400">
				{$i18n.t('Answer a few questions and the assistant will be drafted for you.')}
			</div>
		</div>

		<div class="min-h-[6rem]">
			<Messages
				{chatId}
				className="w-full"
				user={$user}
				bind:history
				bind:prompt
				selectedModels={['']}
				atSelectedModel={undefined}
				autoScroll={true}
				readOnly={true}
				sendMessage={() => {}}
				continueResponse={() => {}}
				regenerateResponse={() => {}}
				mergeResponses={() => {}}
				chatActionHandler={() => {}}
				showMessage={() => {}}
				submitMessage={() => {}}
				addMessages={() => {}}
				setInputText={() => {}}
			/>
		</div>

		<div class="flex flex-col gap-1.5">
			<div class="text-xs font-medium text-gray-500">
				{$i18n.t('Knowledge for the assistant')}
			</div>
			<Knowledge bind:selectedItems={knowledge} />
		</div>

		<div
			class="flex gap-1 items-end rounded-3xl border border-gray-100 dark:border-gray-800 bg-gray-50 dark:bg-gray-850 px-2.5 py-1.5"
		>
			<textarea
				class="flex-1 bg-transparent outline-hidden resize-none px-2 py-2 text-sm"
				rows="1"
				bind:value={input}
				disabled={streaming}
				placeholder={$i18n.t('Type your answer...')}
				on:keydown={(e) => {
					if (e.key === 'Enter' && !e.shiftKey) {
						e.preventDefault();
						sendHandler();
					}
				}}
			></textarea>
			<button
				class="rounded-full p-2 bg-black text-white dark:bg-white dark:text-black transition disabled:opacity-40"
				disabled={streaming || input.trim() === ''}
				on:click={sendHandler}
				aria-label={$i18n.t('Send')}
			>
				<svg
					xmlns="http://www.w3.org/2000/svg"
					viewBox="0 0 20 20"
					fill="currentColor"
					class="size-4"
				>
					<path
						fill-rule="evenodd"
						d="M10 17a.75.75 0 0 1-.75-.75V5.612L5.29 9.77a.75.75 0 0 1-1.08-1.04l5.25-5.5a.75.75 0 0 1 1.08 0l5.25 5.5a.75.75 0 1 1-1.08 1.04l-3.96-4.158V16.25A.75.75 0 0 1 10 17Z"
						clip-rule="evenodd"
					/>
				</svg>
			</button>
		</div>
	</div>
</div>

<style>
	/* Strip the chat message action rows (copy / download / etc.) — the
	   interview transcript is not a savable chat. */
	:global(.onboarding-chat .buttons) {
		display: none !important;
	}
	:global(.onboarding-chat .group-hover\:visible) {
		display: none !important;
	}
</style>
