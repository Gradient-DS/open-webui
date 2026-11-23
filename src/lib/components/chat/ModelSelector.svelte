<script lang="ts">
	import { config, models, pinnedModels, settings, user } from '$lib/stores';
	// [Gradient] The picker contains LLMs only and disables compare for assistant chats.
	import {
		activeAssistant,
		activeAssistantId,
		assistantDefaultModelBlocked,
		llmSelection
	} from '$lib/stores/assistant';
	import { pickableLLMId, pickableLLMs } from '$lib/utils/assistants';
	import { getContext } from 'svelte';
	import { toast } from 'svelte-sonner';
	import Selector from './ModelSelector/Selector.svelte';

	import { updateUserSettings } from '$lib/apis/users';
	import equal from 'fast-deep-equal';
	const i18n = getContext('i18n');

	export let selectedModels = [''];
	export let disabled = false;

	export let showSetDefault = true;
	export let triggerClassName = 'text-lg';
	export let className = undefined;
	export let placement: 'top' | 'bottom' | 'auto' = 'bottom';
	export let align: 'start' | 'end' = 'start';

	let compareModels = selectedModels.length > 1;
	let selector;

	export const open = () => selector?.open();

	const saveDefaultModel = async () => {
		const hasEmptyModel = selectedModels.filter((it) => it === '');
		if (hasEmptyModel.length) {
			toast.error($i18n.t('Choose a model before saving...'));
			return;
		}
		settings.set({ ...$settings, models: selectedModels });
		await updateUserSettings(localStorage.token, { ui: $settings });

		toast.success($i18n.t('Default model updated'));
	};

	const pinModelHandler = async (modelId) => {
		settings.set({
			...$settings,
			pinnedModels: $pinnedModels.includes(modelId)
				? $pinnedModels.filter((id) => id !== modelId)
				: [...$pinnedModels, modelId]
		});
		await updateUserSettings(localStorage.token, { ui: $settings });
	};

	// [Gradient] An assistant that uses tools runs only on a model that can call them: the others
	// leave the picker, and a selected one makes way for the default model that can.
	$: if (selectedModels.length > 0 && $models.length > 0) {
		const preferred = [...($settings?.models ?? []), ...($config?.default_models ?? '').split(',')];
		const _selectedModels = llmSelection(selectedModels).map((model) =>
			$models.map((m) => m.id).includes(model)
				? pickableLLMId(model, $models, $activeAssistant, preferred)
				: ''
		);

		if (!equal(_selectedModels, selectedModels)) {
			selectedModels = _selectedModels;
		}
	}

	// [Gradient] Explicit dependency also collapses compare when an assistant is dropped.
	$: if ($activeAssistantId) {
		compareModels = false;
		if (selectedModels.length > 1) selectedModels = selectedModels.slice(0, 1);
	}

	$: if (!$activeAssistantId && selectedModels.length > 1 && !compareModels) {
		compareModels = true;
	}
</script>

<div class="flex min-w-0 max-w-full flex-col items-start">
	<div class="flex min-w-0 max-w-full">
		<div class="min-w-0 max-w-full overflow-hidden">
			<div class="min-w-0 max-w-full">
				<!-- [Gradient] Assistant identity is shown by AssistantChip. -->
				<Selector
					bind:this={selector}
					id="model"
					placeholder={$i18n.t('Select a model')}
					items={pickableLLMs($models, $activeAssistant).map((model) => ({
						value: model.id,
						label: model.name,
						model: model
					}))}
					{pinModelHandler}
					{className}
					{triggerClassName}
					{placement}
					{align}
					{showSetDefault}
					onSetDefault={saveDefaultModel}
					on:select={() => assistantDefaultModelBlocked.set(true)}
					multipleEnabled={!$activeAssistantId &&
						($user?.role === 'admin' || ($user?.permissions?.chat?.multiple_models ?? true))}
					{disabled}
					bind:compareEnabled={compareModels}
					bind:values={selectedModels}
				/>
			</div>
		</div>
	</div>
</div>
