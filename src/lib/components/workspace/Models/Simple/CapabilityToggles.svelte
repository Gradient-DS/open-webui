<script lang="ts">
	import { getContext } from 'svelte';
	import Switch from '$lib/components/common/Switch.svelte';
	import type { AssistantToggles } from '$lib/utils/assistantCapabilities';

	const i18n = getContext('i18n');

	export let toggles: AssistantToggles;

	const rows: { key: keyof AssistantToggles; icon: string; label: string }[] = [
		{ key: 'web_search', icon: '🌐', label: 'Search the web' },
		{ key: 'image_generation', icon: '🎨', label: 'Generate images' },
		{ key: 'code_interpreter', icon: '💻', label: 'Run code & analyze data' },
		{ key: 'vision', icon: '👁️', label: 'Understand images' },
		{ key: 'file_upload', icon: '📎', label: 'Read uploaded files' },
		{ key: 'citations', icon: '🔗', label: 'Show sources' }
	];
</script>

<div class="flex flex-col gap-2.5">
	{#each rows as row}
		<div class="flex items-center justify-between">
			<div class="text-sm">{row.icon}&nbsp; {$i18n.t(row.label)}</div>
			<Switch bind:state={toggles[row.key]} />
		</div>
	{/each}
</div>
