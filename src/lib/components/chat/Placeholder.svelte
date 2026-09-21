<script lang="ts">
	// [Gradient] Suggestions use effective LLMs; presentation uses assistant identity.
	import { effectiveModels as _models, activeAssistant } from '$lib/stores/assistant';
	import type { Model } from '$lib/stores';
	import type {
		ChatAttachment,
		ChatInputCallbacks,
		AskUserPrompt
	} from '$lib/types/chatAttachment';
	import { marked } from 'marked';
	import DOMPurify from 'dompurify';

	import { getContext, createEventDispatcher } from 'svelte';
	import { fade } from 'svelte/transition';

	const dispatch = createEventDispatcher();

	import {
		config,
		user,
		temporaryChatEnabled,
		selectedFolder,
		pendingAgentId
	} from '$lib/stores';
	import { refreshChatList, refreshFolderChatLists } from '$lib/stores/chatList';
	import { sanitizeResponseContent } from '$lib/utils';
	import { resolveLocalized } from '$lib/utils/localized';
	import { isAgentRouted, isFeatureEnabled } from '$lib/utils/features';
	import { WEBUI_API_BASE_URL } from '$lib/constants';

	import Suggestions from './Suggestions.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import EyeSlash from '$lib/components/icons/EyeSlash.svelte';
	import MessageInput from './MessageInput.svelte';
	import FolderPlaceholder from './Placeholder/FolderPlaceholder.svelte';
	import FolderTitle from './Placeholder/FolderTitle.svelte';
	import WelcomeMessage from './WelcomeMessage.svelte';

	const i18n = getContext('i18n');

	export let createMessagePair: ChatInputCallbacks['createMessagePair'];
	export let stopResponse: ChatInputCallbacks['stopResponse'];

	export let autoScroll = false;

	export let atSelectedModel: Model | undefined;
	export let selectedModels: [''];

	export let history;

	export let prompt = '';
	export let files = [];
	export let messageInput = null;

	export let selectedToolIds = [];
	export let selectedSkillIds = [];
	export let selectedFilterIds = [];
	export let pendingOAuthTools = [];

	export let showCommands = false;

	export let imageGenerationEnabled = false;
	export let codeInterpreterEnabled = false;
	export let documentWriterEnabled = false;
	export let webSearchEnabled = false;
	export let toolApprovalMode = 'full';
	export let onToolApprovalModeChange: ChatInputCallbacks['onToolApprovalModeChange'] = () => {};
	export let oauthRedirectHandler: ChatInputCallbacks['oauthRedirectHandler'] = () => {};

	export let onUpload: ChatInputCallbacks['onUpload'] = () => {};
	export let onUpdate: (data?: { file?: ChatAttachment }) => void = () => {};
	export let onSelect: (event?: unknown) => void = () => {};
	export let onChange: ChatInputCallbacks['onChange'] = () => {};
	export let onWebSearchToggle: ChatInputCallbacks['onWebSearchToggle'] = () => {};
	export let messageQueue: { id: string; prompt: string; files: ChatAttachment[] }[] = [];
	export let onQueueSendNow: (id: string) => void = () => {};
	export let onQueueEdit: (id: string) => void = () => {};
	export let onQueueDelete: (id: string) => void = () => {};
	export let askUser: AskUserPrompt = {
		show: false,
		questions: [],
		allowOther: true,
		timeoutMs: null,
		onConfirm: () => {},
		onCancel: () => {}
	};

	export let dragged = false;

	let models = [];
	let selectedModelIdx = 0;
	// [Gradient] Keep the LLM id out of assistant avatar URLs and greetings.
	$: greetingModel = $activeAssistant ?? $_models.find((m) => m.id === selectedModels[selectedModelIdx]);

	$: if (selectedModels.length > 0) {
		selectedModelIdx = models.length - 1;
	}

	$: models = selectedModels.map((id) => $_models.find((m) => m.id === id));

	// Agent picker adds a content block below the input, welcome message adds
	// one above — either way, shrink the placeholder's vertical padding so the
	// screen still fits without scrolling.
	$: agentPickerEnabled =
		isFeatureEnabled('agent_picker') && Boolean($config?.features?.feature_agent_api_enabled);
	$: welcomeMessageEnabled = $config?.features?.enable_welcome_message === true;
	$: compactPlaceholder = agentPickerEnabled || welcomeMessageEnabled;

	// True when viewing a shared folder the current user doesn't own AND lacks write access
	$: folderReadOnly =
		$selectedFolder != null &&
		$selectedFolder.user_id !== $user?.id &&
		$selectedFolder.permission !== 'write';
</script>

<div
	class="m-auto w-full max-w-[58rem] px-1 @2xl:px-20 translate-y-6 {compactPlaceholder
		? 'py-12'
		: 'py-24'} text-center"
>
	{#if $temporaryChatEnabled}
		<Tooltip
			content={$i18n.t("This chat won't appear in history and your messages will not be saved.")}
			className="w-full flex justify-center mb-0.5"
			placement="top"
		>
			<div class="flex items-center gap-1.5 text-gray-500 text-xs my-1 w-fit">
				<EyeSlash strokeWidth="2" className="size-3.5" />{$i18n.t('Temporary Chat')}
			</div>
		</Tooltip>
	{/if}

	<div class="w-full text-3xl text-gray-800 dark:text-gray-100 text-center flex items-center gap-4">
		<div class="w-full flex flex-col justify-center items-center">
			{#if $selectedFolder}
				<FolderTitle
					folder={$selectedFolder}
					readOnly={folderReadOnly}
					onUpdate={async () => {
						await Promise.all([refreshChatList(localStorage.token), refreshFolderChatLists(null)]);
					}}
					onDelete={async () => {
						await Promise.all([refreshChatList(localStorage.token), refreshFolderChatLists(null)]);

						selectedFolder.set(null);
					}}
				/>
			{:else}
				<div class="flex flex-row justify-center gap-2.5 @sm:gap-3 w-fit px-5 max-w-xl">
					<!-- [Gradient] With the agent picker owning routing, the model
					     behind the chat is an implementation detail — suppress the
					     model avatar/name greeting (raw model ids and favicon
					     fallbacks read as noise) in favor of the plain hello. -->
					{#if !agentPickerEnabled}
						<div class="flex shrink-0 justify-center">
							<div class="flex -space-x-4 mb-0.5" in:fade={{ duration: 100 }}>
								{#each models as model, modelIdx}
									<Tooltip
										content={(models[modelIdx]?.info?.meta?.tags ?? [])
											.map((tag) => tag.name.toUpperCase())
											.join(', ')}
										placement="top"
									>
										<button
											aria-hidden={models.length <= 1}
											aria-label={$i18n.t('Get information on {{name}} in the UI', {
												name: ($activeAssistant ?? models[modelIdx])?.name
											})}
											on:click={() => {
												selectedModelIdx = modelIdx;
											}}
										>
											<img
												alt=""
												src={`${WEBUI_API_BASE_URL}/models/model/profile/image?id=${($activeAssistant ?? model)?.id}&lang=${$i18n.language}`}
												class=" size-9 @sm:size-10 rounded-2xl"
												aria-hidden="true"
												draggable="false"
												on:error={(e) => {
													// LICENSE covers this Open WebUI fallback logo.
													// Do not alter, remove, obscure, or replace it except as LICENSE permits:
													// https://docs.openwebui.com/license.
													e.currentTarget.src = '/favicon.png';
												}}
											/>
										</button>
									</Tooltip>
								{/each}
							</div>
						</div>
					{/if}

					<div
						class=" text-2xl @sm:text-2xl line-clamp-1 flex items-center"
						in:fade={{ duration: 100 }}
					>
						{#if $config?.ui?.greeting_template && !$activeAssistant}
							{resolveLocalized($config.ui.greeting_template, $i18n?.language).replace(
								'{{name}}',
								$user?.name ?? ''
							)}
						{:else if !agentPickerEnabled && greetingModel?.name}
							<Tooltip
								content={greetingModel?.name}
								placement="top"
								className=" flex items-center "
							>
								<span class="line-clamp-1">
									{greetingModel?.name}
								</span>
							</Tooltip>
						{:else}
							{$i18n.t('Hello, {{name}}', { name: $user?.name })}
						{/if}
					</div>
				</div>

				<div class="flex mt-1 mb-2">
					<div in:fade={{ duration: 100, delay: 50 }}>
						{#if greetingModel?.info?.meta?.description ?? null}
							<Tooltip
								className=" w-fit"
								content={DOMPurify.sanitize(
									marked.parse(
										sanitizeResponseContent(
											greetingModel?.info?.meta?.description ?? ''
										).replaceAll('\n', '<br>')
									)
								)}
								placement="top"
							>
								<div
									class="mt-0.5 px-2 text-sm font-normal text-gray-500 dark:text-gray-400 line-clamp-2 max-w-xl markdown"
								>
									<!-- eslint-disable-next-line svelte/no-at-html-tags -- Content is sanitized with DOMPurify. -->
									{@html DOMPurify.sanitize(
										marked.parse(
											sanitizeResponseContent(
												greetingModel?.info?.meta?.description ?? ''
											).replaceAll('\n', '<br>')
										)
									)}
								</div>
							</Tooltip>

							{#if models[selectedModelIdx]?.info?.meta?.user}
								<div class="mt-0.5 text-sm font-normal text-gray-400 dark:text-gray-500">
									By
									{#if models[selectedModelIdx]?.info?.meta?.user.community}
										<a
											href="https://openwebui.com/m/{models[selectedModelIdx]?.info?.meta?.user
												.username}"
											>{models[selectedModelIdx]?.info?.meta?.user.name
												? models[selectedModelIdx]?.info?.meta?.user.name
												: `@${models[selectedModelIdx]?.info?.meta?.user.username}`}</a
										>
									{:else}
										{models[selectedModelIdx]?.info?.meta?.user.name}
									{/if}
								</div>
							{/if}
						{/if}
					</div>
				</div>
			{/if}

			<div class="@md:max-w-3xl w-full text-left">
				<WelcomeMessage />
			</div>

			<div class="text-base font-normal @md:max-w-3xl w-full py-3 {atSelectedModel ? 'mt-2' : ''}">
				{#if !($selectedFolder && folderReadOnly)}
					<MessageInput
						bind:this={messageInput}
						agentRouted={isAgentRouted($pendingAgentId)}
						agentPickerActive={agentPickerEnabled}
						{history}
						bind:selectedModels
						bind:files
						bind:prompt
						bind:autoScroll
						bind:selectedToolIds
						bind:selectedSkillIds
						bind:selectedFilterIds
						bind:imageGenerationEnabled
						bind:codeInterpreterEnabled
						bind:documentWriterEnabled
						bind:webSearchEnabled
						bind:atSelectedModel
						bind:showCommands
						bind:dragged
						{pendingOAuthTools}
						{oauthRedirectHandler}
						{toolApprovalMode}
						{onToolApprovalModeChange}
						{stopResponse}
						{createMessagePair}
						placeholder={$i18n.t('How can I help you today?')}
						{onChange}
						{onUpload}
						{onUpdate}
						{messageQueue}
						{onQueueSendNow}
						{onQueueEdit}
						{onQueueDelete}
						{askUser}
						{onWebSearchToggle}
						on:chatVariables
						on:submit={(e) => {
							dispatch('submit', e.detail);
						}}
					/>
				{/if}
			</div>
		</div>
	</div>

	{#if $selectedFolder}
		<div class="mx-auto px-4 md:max-w-3xl md:px-6 min-h-62" in:fade={{ duration: 200, delay: 200 }}>
			<FolderPlaceholder folder={$selectedFolder} />
		</div>
	{:else}
		<div class="mx-auto max-w-2xl mt-2" in:fade={{ duration: 200, delay: 200 }}>
			<div class="mx-5">
				<Suggestions
					suggestionPrompts={atSelectedModel?.info?.meta?.suggestion_prompts ??
						models[selectedModelIdx]?.info?.meta?.suggestion_prompts ??
						$config?.default_prompt_suggestions ??
						[]}
					inputValue={prompt}
					{onSelect}
				/>
			</div>
		</div>
	{/if}
</div>
