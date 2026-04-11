<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { models, settings, user, config } from '$lib/stores';
	import { createEventDispatcher, onMount, getContext, tick } from 'svelte';

	const dispatch = createEventDispatcher();
	import { getModels } from '$lib/apis';
	import { getConfig, updateConfig } from '$lib/apis/evaluations';

	import Switch from '$lib/components/common/Switch.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Plus from '$lib/components/icons/Plus.svelte';
	import Model from './Evaluations/Model.svelte';
	import ArenaModelModal from './Evaluations/ArenaModelModal.svelte';

	const i18n = getContext('i18n');

	let evaluationConfig = null;
	let showAddModel = false;

	const submitHandler = async () => {
		evaluationConfig = await updateConfig(localStorage.token, evaluationConfig).catch((err) => {
			toast.error(err);
			return null;
		});

		if (evaluationConfig) {
			toast.success($i18n.t('Settings saved successfully!'));
			models.set(
				await getModels(
					localStorage.token,
					$config?.features?.enable_direct_connections && ($settings?.directConnections ?? null)
				)
			);
		}
	};

	const addModelHandler = async (model) => {
		evaluationConfig.EVALUATION_ARENA_MODELS.push(model);
		evaluationConfig.EVALUATION_ARENA_MODELS = [...evaluationConfig.EVALUATION_ARENA_MODELS];

		await submitHandler();
		models.set(
			await getModels(
				localStorage.token,
				$config?.features?.enable_direct_connections && ($settings?.directConnections ?? null)
			)
		);
	};

	const editModelHandler = async (model, modelIdx) => {
		evaluationConfig.EVALUATION_ARENA_MODELS[modelIdx] = model;
		evaluationConfig.EVALUATION_ARENA_MODELS = [...evaluationConfig.EVALUATION_ARENA_MODELS];

		await submitHandler();
		models.set(
			await getModels(
				localStorage.token,
				$config?.features?.enable_direct_connections && ($settings?.directConnections ?? null)
			)
		);
	};

	const deleteModelHandler = async (modelIdx) => {
		evaluationConfig.EVALUATION_ARENA_MODELS = evaluationConfig.EVALUATION_ARENA_MODELS.filter(
			(m, mIdx) => mIdx !== modelIdx
		);

		await submitHandler();
		models.set(
			await getModels(
				localStorage.token,
				$config?.features?.enable_direct_connections && ($settings?.directConnections ?? null)
			)
		);
	};

	onMount(async () => {
		if ($user?.role === 'admin') {
			evaluationConfig = await getConfig(localStorage.token).catch((err) => {
				toast.error(err);
				return null;
			});
		}
	});
</script>

<ArenaModelModal
	bind:show={showAddModel}
	on:submit={async (e) => {
		addModelHandler(e.detail);
	}}
/>

<form
	class="flex flex-col h-full justify-between text-sm"
	on:submit|preventDefault={() => {
		submitHandler();
		dispatch('save');
	}}
>
	<div class="overflow-y-scroll scrollbar-hidden h-full">
		{#if evaluationConfig !== null}
			<div class="">
				<div class="mb-3">
					<div class=" mt-0.5 mb-2.5 text-base font-medium">{$i18n.t('General')}</div>

					<hr class=" border-gray-100/30 dark:border-gray-850/30 my-2" />

					<div class="mb-2.5 flex w-full justify-between">
						<div class=" text-xs font-medium">{$i18n.t('Arena Models')}</div>

						<Tooltip content={$i18n.t(`Message rating should be enabled to use this feature`)}>
							<Switch bind:state={evaluationConfig.ENABLE_EVALUATION_ARENA_MODELS} />
						</Tooltip>
					</div>
				</div>

				{#if evaluationConfig.ENABLE_EVALUATION_ARENA_MODELS}
					<div class="mb-3">
						<div class=" mt-0.5 mb-2.5 text-base font-medium flex justify-between items-center">
							<div>
								{$i18n.t('Manage')}
							</div>

							<div>
								<Tooltip content={$i18n.t('Add Arena Model')}>
									<button
										class="p-1"
										type="button"
										on:click={() => {
											showAddModel = true;
										}}
									>
										<Plus />
									</button>
								</Tooltip>
							</div>
						</div>

						<hr class=" border-gray-100/30 dark:border-gray-850/30 my-2" />

						<div class="flex flex-col gap-2">
							{#if (evaluationConfig?.EVALUATION_ARENA_MODELS ?? []).length > 0}
								{#each evaluationConfig.EVALUATION_ARENA_MODELS as model, index}
									<Model
										{model}
										on:edit={(e) => {
											editModelHandler(e.detail, index);
										}}
										on:delete={(e) => {
											deleteModelHandler(index);
										}}
									/>
								{/each}
							{:else}
								<div class=" text-center text-xs text-gray-500">
									{$i18n.t(
										`Using the default arena model with all models. Click the plus button to add custom models.`
									)}
								</div>
							{/if}
						</div>
					</div>
				{/if}
			</div>

			<!-- Feedback Configuration Section -->
			<div class="mb-3">
				<div class="mt-0.5 mb-2.5 text-base font-medium">{$i18n.t('Feedback')}</div>
				<hr class="border-gray-100/30 dark:border-gray-850/30 my-2" />

				<!-- Layer 1: Thumbs Up/Down -->
				<div class="mb-2.5 flex w-full justify-between">
					<div class="flex flex-col">
						<div class="text-xs font-medium">{$i18n.t('Message Rating (Thumbs Up/Down)')}</div>
						<div class="text-xs text-gray-500">{$i18n.t('Allow users to rate individual responses')}</div>
					</div>
					<Switch bind:state={evaluationConfig.ENABLE_MESSAGE_RATING} />
				</div>

				<!-- Layer 2: Issue Tags -->
				<div class="mb-2.5 flex w-full justify-between">
					<div class="flex flex-col">
						<div class="text-xs font-medium">{$i18n.t('Feedback Tags')}</div>
						<div class="text-xs text-gray-500">{$i18n.t('Custom tags shown after rating a response')}</div>
					</div>
					<Switch bind:state={evaluationConfig.ENABLE_FEEDBACK_LAYER2} />
				</div>

				{#if evaluationConfig.ENABLE_FEEDBACK_LAYER2}
					<div class="ml-2 mb-3">
						<!-- Positive Tags -->
						<div class="text-xs font-medium mb-1">{$i18n.t('Positive Tags')}</div>
						<div class="text-xs text-gray-500 mb-2">
							{$i18n.t('Tags shown after thumbs up. Leave empty to use defaults.')}
						</div>
						{#each evaluationConfig.FEEDBACK_LAYER2_POSITIVE_TAGS ?? [] as tag, index}
							<div class="flex items-center gap-2 mb-1.5">
								<input
									class="flex-1 text-sm px-2.5 py-1 bg-transparent border border-gray-100/30 dark:border-gray-850/30 rounded-lg outline-hidden"
									bind:value={tag.label}
									on:input={() => {
										tag.key = tag.label.toLowerCase().replace(/\s+/g, '_').replace(/[^a-z0-9_]/g, '');
									}}
									placeholder={$i18n.t('Tag label')}
								/>
								<button
									type="button"
									class="p-1 text-gray-400 hover:text-red-500 transition"
									on:click={() => {
										evaluationConfig.FEEDBACK_LAYER2_POSITIVE_TAGS = evaluationConfig.FEEDBACK_LAYER2_POSITIVE_TAGS.filter((_, i) => i !== index);
									}}
								>
									<svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
										<path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
									</svg>
								</button>
							</div>
						{/each}
						<button
							type="button"
							class="text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 flex items-center gap-1 mt-1"
							on:click={() => {
								if (!evaluationConfig.FEEDBACK_LAYER2_POSITIVE_TAGS) evaluationConfig.FEEDBACK_LAYER2_POSITIVE_TAGS = [];
								const key = `tag_${Date.now()}`;
								evaluationConfig.FEEDBACK_LAYER2_POSITIVE_TAGS = [...evaluationConfig.FEEDBACK_LAYER2_POSITIVE_TAGS, { key, label: '' }];
							}}
						>
							<Plus className="size-3" /> {$i18n.t('Add tag')}
						</button>

						<!-- Negative Tags -->
						<div class="text-xs font-medium mb-1 mt-3">{$i18n.t('Negative Tags')}</div>
						<div class="text-xs text-gray-500 mb-2">
							{$i18n.t('Tags shown after thumbs down. Leave empty to use defaults.')}
						</div>
						{#each evaluationConfig.FEEDBACK_LAYER2_NEGATIVE_TAGS ?? [] as tag, index}
							<div class="flex items-center gap-2 mb-1.5">
								<input
									class="flex-1 text-sm px-2.5 py-1 bg-transparent border border-gray-100/30 dark:border-gray-850/30 rounded-lg outline-hidden"
									bind:value={tag.label}
									on:input={() => {
										tag.key = tag.label.toLowerCase().replace(/\s+/g, '_').replace(/[^a-z0-9_]/g, '');
									}}
									placeholder={$i18n.t('Tag label')}
								/>
								<button
									type="button"
									class="p-1 text-gray-400 hover:text-red-500 transition"
									on:click={() => {
										evaluationConfig.FEEDBACK_LAYER2_NEGATIVE_TAGS = evaluationConfig.FEEDBACK_LAYER2_NEGATIVE_TAGS.filter((_, i) => i !== index);
									}}
								>
									<svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
										<path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
									</svg>
								</button>
							</div>
						{/each}
						<button
							type="button"
							class="text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 flex items-center gap-1 mt-1"
							on:click={() => {
								if (!evaluationConfig.FEEDBACK_LAYER2_NEGATIVE_TAGS) evaluationConfig.FEEDBACK_LAYER2_NEGATIVE_TAGS = [];
								const key = `tag_${Date.now()}`;
								evaluationConfig.FEEDBACK_LAYER2_NEGATIVE_TAGS = [...evaluationConfig.FEEDBACK_LAYER2_NEGATIVE_TAGS, { key, label: '' }];
							}}
						>
							<Plus className="size-3" /> {$i18n.t('Add tag')}
						</button>
					</div>
				{/if}

				<!-- Layer 3: Free Text -->
				<div class="mb-2.5 flex w-full justify-between">
					<div class="flex flex-col">
						<div class="text-xs font-medium">{$i18n.t('Free Text Comment')}</div>
						<div class="text-xs text-gray-500">{$i18n.t('Allow users to leave a text comment on responses')}</div>
					</div>
					<Switch bind:state={evaluationConfig.ENABLE_FEEDBACK_LAYER3} />
				</div>

				{#if evaluationConfig.ENABLE_FEEDBACK_LAYER3}
					<div class="ml-2 mb-3">
						<div class="text-xs text-gray-500 mb-1">{$i18n.t('Custom prompt text (optional)')}</div>
						<input
							class="w-full text-sm px-2.5 py-1.5 bg-transparent border border-gray-100/30 dark:border-gray-850/30 rounded-lg outline-hidden"
							bind:value={evaluationConfig.FEEDBACK_LAYER3_PROMPT}
							placeholder={$i18n.t('Feel free to add specific details')}
						/>
					</div>
				{/if}

				<!-- Category Tags -->
				<div class="mb-2.5 flex w-full justify-between">
					<div class="flex flex-col">
						<div class="text-xs font-medium">{$i18n.t('Category Tags')}</div>
						<div class="text-xs text-gray-500">{$i18n.t('Allow users to add free-form category tags to feedback')}</div>
					</div>
					<Switch bind:state={evaluationConfig.ENABLE_FEEDBACK_CATEGORY_TAGS} />
				</div>

				<hr class="border-gray-100/30 dark:border-gray-850/30 my-2" />

				<!-- Conversation-Level Feedback -->
				<div class="mb-2.5 flex w-full justify-between">
					<div class="flex flex-col">
						<div class="text-xs font-medium">{$i18n.t('Conversation Feedback')}</div>
						<div class="text-xs text-gray-500">{$i18n.t('Show a feedback strip above the input after 2+ messages')}</div>
					</div>
					<Switch bind:state={evaluationConfig.ENABLE_CONVERSATION_FEEDBACK} />
				</div>

				{#if evaluationConfig.ENABLE_CONVERSATION_FEEDBACK}
					<div class="ml-2 mb-3">
						<div class="flex items-center gap-2 mb-2">
							<div class="text-xs text-gray-500">{$i18n.t('Scale')}</div>
							<span class="text-xs">1 –</span>
							<input
								type="number"
								class="w-16 text-sm px-2 py-1 bg-transparent border border-gray-100/30 dark:border-gray-850/30 rounded-lg outline-hidden"
								bind:value={evaluationConfig.CONVERSATION_FEEDBACK_SCALE_MAX}
								min="2"
								max="10"
							/>
						</div>
						<div class="text-xs text-gray-500 mb-1">{$i18n.t('Header text (optional)')}</div>
						<input
							class="w-full text-sm px-2.5 py-1.5 bg-transparent border border-gray-100/30 dark:border-gray-850/30 rounded-lg outline-hidden mb-2"
							bind:value={evaluationConfig.CONVERSATION_FEEDBACK_HEADER}
							placeholder={$i18n.t('How was this conversation?')}
						/>
						<div class="text-xs text-gray-500 mb-1">{$i18n.t('Placeholder text (optional)')}</div>
						<input
							class="w-full text-sm px-2.5 py-1.5 bg-transparent border border-gray-100/30 dark:border-gray-850/30 rounded-lg outline-hidden"
							bind:value={evaluationConfig.CONVERSATION_FEEDBACK_PLACEHOLDER}
							placeholder={$i18n.t('Any thoughts on the overall conversation?')}
						/>
					</div>
				{/if}
			</div>
		{:else}
			<div class="flex h-full justify-center">
				<div class="my-auto">
					<Spinner className="size-6" />
				</div>
			</div>
		{/if}
	</div>

	<div class="flex justify-end pt-3 text-sm font-medium">
		<button
			class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full"
			type="submit"
		>
			{$i18n.t('Save')}
		</button>
	</div>
</form>
