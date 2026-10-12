<script lang="ts">
	import { getContext } from 'svelte';
	import { config } from '$lib/stores';
	import Switch from '$lib/components/common/Switch.svelte';
	import type { AssistantToggles } from '$lib/utils/assistantCapabilities';

	const i18n = getContext('i18n');

	export let toggles: AssistantToggles;

	// Capabilities gated behind an instance feature flag — only shown
	// when the deployment has that feature enabled (mirrors the gating
	// in the advanced editor's Capabilities.svelte).
	const configGuards: Partial<Record<keyof AssistantToggles, string>> = {
		web_search: 'enable_web_search',
		image_generation: 'enable_image_generation',
		code_interpreter: 'enable_code_interpreter',
		document_writer: 'enable_document_writer'
	};

	const allRows: { key: keyof AssistantToggles; icon: string; label: string }[] = [
		{ key: 'web_search', icon: '🌐', label: 'Search the web' },
		{ key: 'image_generation', icon: '🎨', label: 'Generate images' },
		{ key: 'code_interpreter', icon: '💻', label: 'Run code & analyze data' },
		{ key: 'document_writer', icon: '📝', label: 'Write documents' },
		{ key: 'vision', icon: '👁️', label: 'Understand images' },
		{ key: 'file_upload', icon: '📎', label: 'Read uploaded files' },
		{ key: 'citations', icon: '🔗', label: 'Show sources' }
	];

	// Drop any capability whose instance feature flag is off.
	$: rows = allRows.filter((row) => {
		const guard = configGuards[row.key];
		return !guard || !!($config?.features as any)?.[guard];
	});
</script>

<div class="flex flex-col gap-2.5">
	{#each rows as row}
		<div class="flex items-center justify-between">
			<div class="text-sm">{row.icon}&nbsp; {$i18n.t(row.label)}</div>
			<Switch bind:state={toggles[row.key]} />
		</div>
	{/each}
</div>
