<script lang="ts">
	import { getContext } from 'svelte';
	import { config } from '$lib/stores';
	import Checkbox from '$lib/components/common/Checkbox.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { marked } from 'marked';

	const i18n = getContext('i18n');

	// Map tool keys to config feature flags
	const toolConfigGuards: Record<string, string> = {
		memory: 'enable_memories',
		notes: 'enable_notes',
		channels: 'enable_channels',
		knowledge: 'feature_knowledge',
		web_search: 'enable_web_search',
		image_generation: 'enable_image_generation',
		code_interpreter: 'enable_code_interpreter'
	};

	const toolLabels = {
		time: {
			label: $i18n.t('Time & Calculation'),
			description: $i18n.t('Get current time and perform date/time calculations')
		},
		memory: {
			label: $i18n.t('Memory'),
			description: $i18n.t('Search and manage user memories')
		},
		chats: {
			label: $i18n.t('Chat History'),
			description: $i18n.t('Search and view user chat history')
		},
		notes: {
			label: $i18n.t('Notes'),
			description: $i18n.t('Search, view, and manage user notes')
		},
		knowledge: {
			label: $i18n.t('Knowledge Base'),
			description: $i18n.t('Browse and query knowledge bases')
		},
		channels: {
			label: $i18n.t('Channels'),
			description: $i18n.t('Search channels and channel messages')
		},
		web_search: {
			label: $i18n.t('Web Search'),
			description: $i18n.t('Search the web and fetch URLs')
		},
		image_generation: {
			label: $i18n.t('Image Generation'),
			description: $i18n.t('Generate and edit images')
		},
		code_interpreter: {
			label: $i18n.t('Code Interpreter'),
			description: $i18n.t('Execute code')
		}
	};

	const allTools = Object.keys(toolLabels);

	export let builtinTools: Record<string, boolean> = {};

	// Filter to only tools whose global feature is enabled
	$: visibleTools = allTools.filter((tool) => {
		const configKey = toolConfigGuards[tool];
		if (configKey && !$config?.features?.[configKey]) {
			return false;
		}
		return true;
	});

	// Initialize missing keys to true (default enabled)
	$: {
		for (const tool of allTools) {
			if (!(tool in builtinTools)) {
				builtinTools[tool] = true;
			}
		}
	}
</script>

<div>
	<div class="flex w-full justify-between mb-1">
		<div class="self-center text-xs font-medium text-gray-500">{$i18n.t('Builtin Tools')}</div>
	</div>
	<div class="flex items-center mt-2 flex-wrap">
		{#each visibleTools as tool}
			<div class="flex items-center gap-2 mr-3">
				<Checkbox
					state={builtinTools[tool] !== false ? 'checked' : 'unchecked'}
					on:change={(e) => {
						builtinTools = {
							...builtinTools,
							[tool]: e.detail === 'checked'
						};
					}}
				/>

				<div class="py-0.5 text-sm">
					<Tooltip content={marked.parse(toolLabels[tool].description)}>
						{$i18n.t(toolLabels[tool].label)}
					</Tooltip>
				</div>
			</div>
		{/each}
	</div>
</div>
