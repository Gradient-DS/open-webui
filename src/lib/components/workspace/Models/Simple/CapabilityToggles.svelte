<script lang="ts">
	import { getContext, type ComponentType } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { config } from '$lib/stores';
	import Switch from '$lib/components/common/Switch.svelte';
	import GlobeAlt from '$lib/components/icons/GlobeAlt.svelte';
	import Photo from '$lib/components/icons/Photo.svelte';
	import Terminal from '$lib/components/icons/Terminal.svelte';
	import Document from '$lib/components/icons/Document.svelte';
	import OneDriveSearch from '$lib/components/icons/OneDriveSearch.svelte';
	import MailSearch from '$lib/components/icons/MailSearch.svelte';
	import Eye from '$lib/components/icons/Eye.svelte';
	import Clip from '$lib/components/icons/Clip.svelte';
	import Link from '$lib/components/icons/Link.svelte';
	import type { AssistantToggles } from '$lib/utils/assistantCapabilities';

	const i18n: Writable<i18nType> = getContext('i18n');

	export let toggles: AssistantToggles;

	// Capabilities gated behind an instance feature flag — only shown
	// when the deployment has that feature enabled (mirrors the gating
	// in the advanced editor's Capabilities.svelte).
	const configGuards: Partial<Record<keyof AssistantToggles, string>> = {
		web_search: 'enable_web_search',
		image_generation: 'enable_image_generation',
		code_interpreter: 'enable_code_interpreter',
		document_writer: 'enable_document_writer',
		live_documents: 'enable_live_documents',
		live_mail: 'enable_live_mail'
	};

	// Labels and icons of composer tools match the chat + menu.
	const allRows: { key: keyof AssistantToggles; icon: ComponentType; label: string }[] = [
		{ key: 'web_search', icon: GlobeAlt, label: 'Search the web' },
		{ key: 'image_generation', icon: Photo, label: 'Generate images' },
		{ key: 'code_interpreter', icon: Terminal, label: 'Run code & analyze data' },
		{ key: 'document_writer', icon: Document, label: 'Document writer' },
		{ key: 'live_documents', icon: OneDriveSearch, label: 'OneDrive search' },
		{ key: 'live_mail', icon: MailSearch, label: 'Mail search' },
		{ key: 'vision', icon: Eye, label: 'Understand images' },
		{ key: 'file_upload', icon: Clip, label: 'Read uploaded files' },
		{ key: 'citations', icon: Link, label: 'Show sources' }
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
			<div class="flex items-center gap-2 text-sm">
				<svelte:component this={row.icon} className="size-4" />
				{$i18n.t(row.label)}
			</div>
			<Switch bind:state={toggles[row.key]} />
		</div>
	{/each}
</div>
