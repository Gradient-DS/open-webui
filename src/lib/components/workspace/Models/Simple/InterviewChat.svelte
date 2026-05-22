<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import { user } from '$lib/stores';
	import { streamOnboarding, type OnboardingMessage } from '$lib/apis/onboarding';
	import Messages from '$lib/components/chat/Messages.svelte';
	import MessageInput from '$lib/components/chat/MessageInput.svelte';

	const i18n = getContext('i18n');

	// Fired with the AssistantDraft once the interview completes.
	export let onComplete: (draft: any) => void;
	// Fired if the onboarding agent is unreachable.
	export let onUnavailable: () => void;

	const chatId = `onb-${crypto.randomUUID()}`;

	// agentTranscript = what we send to the agent (includes the hidden seed
	// turn). history = the on-screen chat tree rendered by Messages.svelte.
	let agentTranscript: OnboardingMessage[] = [];
	let history: { messages: Record<string, any>; currentId: string | null } = {
		messages: {},
		currentId: null
	};
	let prompt = '';
	let files: any[] = [];
	let selectedModels: [''] = [''];
	let messageInput: any;
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
				? {
						model: 'Soev Assistant Builder',
						modelName: 'Soev Assistant Builder',
						modelIdx: 0,
						done: false
					}
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

	/** Receives the submitted text from the real chat MessageInput. */
	const handleSubmit = async (text: string) => {
		if (!text || text.trim() === '' || streaming) return;
		const t = text.trim();
		prompt = '';
		files = [];
		messageInput?.setText?.('');
		agentTranscript = [...agentTranscript, { role: 'user', content: t }];
		appendMessage('user', t);
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

<div class="onboarding-chat flex flex-col h-full w-full">
	<div class="shrink-0 flex flex-col gap-1 px-1 mt-1.5 mb-3">
		<div class="flex items-center text-xl font-medium px-0.5 shrink-0">
			{$i18n.t('Build your assistant')}
		</div>
		<div class="text-xs text-gray-400 px-0.5">
			{$i18n.t('Answer a few questions and the assistant will be drafted for you.')}
		</div>
	</div>

	<div class="flex-1 overflow-auto">
		<Messages
			{chatId}
			className="w-full"
			user={$user}
			bind:history
			{prompt}
			{selectedModels}
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

	<div class="shrink-0 max-w-3xl mx-auto w-full px-3 pb-3">
		<MessageInput
			bind:this={messageInput}
			bind:prompt
			bind:files
			{history}
			{selectedModels}
			atSelectedModel={undefined}
			createMessagePair={() => {}}
			stopResponse={() => {}}
			onUpload={() => {}}
			onChange={() => {}}
			placeholder={$i18n.t('Type your answer...')}
			on:submit={(e) => handleSubmit(e.detail)}
		/>
	</div>
</div>

<style>
	/* Hide the assistant message action row (copy / download) — the
	   interview transcript is not a savable chat. */
	:global(.onboarding-chat .buttons) {
		display: none !important;
	}

	/* Empty the MessageInput toolbar (+ menu, pinned tools, RAG filter,
	   web search) but keep its flex slot, so the send button stays on
	   the right — like the chat input with every control turned off. */
	:global(.onboarding-chat .max-w-\[80\%\] > *) {
		display: none !important;
	}
</style>
