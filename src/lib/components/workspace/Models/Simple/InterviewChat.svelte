<script lang="ts">
	import { getContext, onDestroy, onMount } from 'svelte';
	import { submitPromptSignal, user } from '$lib/stores';
	import { streamOnboarding, type OnboardingMessage } from '$lib/apis/onboarding';
	import Messages from '$lib/components/chat/Messages.svelte';
	import MessageInput from '$lib/components/chat/MessageInput.svelte';

	const i18n = getContext('i18n');

	// Fired with the AssistantDraft once the interview completes.
	export let onComplete: (draft: any) => void;
	// Fired if the onboarding agent is unreachable.
	export let onUnavailable: () => void;
	// Fired when the user opts to skip the interview and set up manually.
	export let onSkip: () => void;

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
	let autoScroll = true;

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
				} else if (event.type === 'ui_block') {
					const block = {
						id: crypto.randomUUID(),
						name: event.name,
						props: event.props
					};
					const existing = history.messages[assistantId].uiBlocks ?? [];
					history.messages[assistantId].uiBlocks = [...existing, block];
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

	// Forward ChoiceBlock clicks (which write to the global
	// submitPromptSignal store) into the interview turn loop.
	// Stale signals from a previous Chat-route interaction are filtered
	// with a monotonic timestamp cursor: we only honor signals strictly
	// newer than this component's mount time.
	const subscribedAt = Date.now();
	const unsubscribeChoice = submitPromptSignal.subscribe((signal) => {
		if (!signal || !signal.text || signal.ts <= subscribedAt) return;
		if (streaming) return;
		const text = signal.text;
		// Reset the store so a re-subscribe (e.g. HMR) doesn't re-fire,
		// and so subsequent listeners don't see this same click.
		submitPromptSignal.set(null);
		handleSubmit(text);
	});

	onDestroy(() => {
		unsubscribeChoice();
	});
</script>

<div id="chat-pane" class="onboarding-chat flex flex-col h-full w-full">
	<div class="shrink-0 flex flex-col gap-1 px-1 mt-1.5 mb-3">
		<div class="flex justify-between items-center">
			<div class="flex items-center text-xl font-medium px-0.5 shrink-0">
				{$i18n.t('Build your assistant')}
			</div>
			<button
				class="flex text-xs items-center px-3 py-1.5 rounded-xl bg-gray-50 hover:bg-gray-100 dark:bg-gray-850 dark:hover:bg-gray-800 dark:text-gray-200 transition font-medium shrink-0"
				type="button"
				on:click={onSkip}
			>
				{$i18n.t('Set up manually')}
			</button>
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
			bind:autoScroll
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

	<div class="shrink-0 w-full pb-3">
		<MessageInput
			bind:this={messageInput}
			bind:prompt
			bind:files
			bind:autoScroll
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

	/* Hide MessageInput toolbar controls that don't apply to the
	   assistant-building flow. The `+` menu (Upload / Knowledge /
	   Webpage / etc.) stays visible so users can attach files and KBs.
	   These selectors are a starting point — finalised during smoke
	   testing if any controls slip through. */

	/* RAG filter button — assistant builder controls its own filters. */
	:global(.onboarding-chat button[aria-label='RAG Filters']),
	:global(.onboarding-chat button[aria-label='RAG-filters']) {
		display: none !important;
	}

	/* Per-model valves — irrelevant during onboarding. */
	:global(.onboarding-chat #model-valves-button) {
		display: none !important;
	}
</style>
