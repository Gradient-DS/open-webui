<script lang="ts">
	import { getContext } from 'svelte';
	import { config } from '$lib/stores';
	import Checkbox from '$lib/components/common/Checkbox.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { marked } from 'marked';

	const i18n = getContext('i18n');

	// Map warning keys to config feature guards
	// Only show warnings for capabilities whose global feature is enabled
	const warningConfigGuards: Record<string, string> = {
		web_search: 'enable_web_search',
		image_generation: 'enable_image_generation',
		code_interpreter: 'enable_code_interpreter'
	};

	const warningLabels: Record<string, { label: string; description: string }> = {
		file_upload: {
			label: $i18n.t('File Upload'),
			description: $i18n.t('Warn before sending files to this model')
		},
		web_search: {
			label: $i18n.t('Web Search'),
			description: $i18n.t('Warn before web search queries with this model')
		},
		knowledge_local: {
			label: $i18n.t('Local Knowledge Base'),
			description: $i18n.t('Warn before sending local knowledge base content to this model')
		},
		knowledge_external: {
			label: $i18n.t('External Knowledge Base'),
			description: $i18n.t('Warn before sending external knowledge base content to this model')
		},
		vision: {
			label: $i18n.t('Vision'),
			description: $i18n.t('Warn before sending images to this model')
		},
		code_interpreter: {
			label: $i18n.t('Code Interpreter'),
			description: $i18n.t('Warn before sending code to this model for execution')
		},
		image_generation: {
			label: $i18n.t('Image Generation'),
			description: $i18n.t('Warn before sending prompts to image generation service')
		}
	};

	const allWarnings = Object.keys(warningLabels);

	export let dataWarnings: Record<string, boolean> = {};
	export let warningMessage: string = '';

	// Filter to only warnings whose global feature is enabled
	$: visibleWarnings = allWarnings.filter((key) => {
		const configKey = warningConfigGuards[key];
		if (configKey && !$config?.features?.[configKey]) {
			return false;
		}
		return true;
	});

	// Initialize missing keys to false (default: no warnings)
	$: {
		for (const key of allWarnings) {
			if (!(key in dataWarnings)) {
				dataWarnings[key] = false;
			}
		}
	}
</script>

<div>
	<div class="mb-1.5 text-xs text-gray-400 dark:text-gray-600">
		{$i18n.t('Data Sovereignty Warnings')}
	</div>
	<div class="mb-2 text-xs text-gray-400 dark:text-gray-600">
		{$i18n.t(
			'Select capabilities that require user acknowledgment before first use in a conversation.'
		)}
	</div>
	<div class="grid grid-cols-1 gap-x-5 gap-y-1 sm:grid-cols-2 lg:grid-cols-3">
		{#each visibleWarnings as key}
			<div class="flex min-h-6 items-center gap-2.5">
				<Checkbox
					state={dataWarnings[key] ? 'checked' : 'unchecked'}
					on:change={(e) => {
						dataWarnings = {
							...dataWarnings,
							[key]: e.detail === 'checked'
						};
					}}
				/>
				<div class="min-w-0 text-left text-xs text-gray-600 dark:text-gray-400">
					<Tooltip content={marked.parse(warningLabels[key].description)}>
						{$i18n.t(warningLabels[key].label)}
					</Tooltip>
				</div>
			</div>
		{/each}
	</div>

	{#if Object.values(dataWarnings).some((v) => v)}
		<div class="mt-3">
			<div class="mb-1 text-xs text-gray-600 dark:text-gray-400">
				{$i18n.t('Warning Message')}
			</div>
			<textarea
				class="w-full resize-none bg-transparent py-1 text-[0.8125rem] text-gray-700 outline-hidden placeholder:text-gray-300 dark:text-gray-300 dark:placeholder:text-gray-700"
				rows="3"
				placeholder={$i18n.t(
					'This model runs on external infrastructure. Uploaded files and conversation content will be processed by an external provider. Do you want to continue?'
				)}
				bind:value={warningMessage}
			/>
		</div>
	{/if}
</div>
