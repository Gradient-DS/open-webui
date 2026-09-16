<script lang="ts">
	// [Gradient] Citation panel shell; variant bodies share this host.
	import { getContext } from 'svelte';
	import type { i18n as I18n } from 'i18next';
	import type { Readable } from 'svelte/store';
	import { citationPanel, citationPanelVariant, showCitationPanel } from '$lib/stores';
	import XMark from '$lib/components/icons/XMark.svelte';
	const i18n = getContext<Readable<I18n>>('i18n');
	export let overlay = false;
</script>

<div class="relative flex flex-col h-full min-h-0">
	<button
		class="self-end p-3"
		aria-label={$i18n.t('Close')}
		on:click={() => {
			showCitationPanel.set(false);
			citationPanel.set(null);
		}}><XMark className="size-4" /></button
	>
	<p class="p-4 text-sm text-gray-500">
		{$i18n.t("Variant '{{name}}' is not built yet", { name: $citationPanelVariant })}
	</p>
	{#if overlay}<div class="absolute inset-0 z-10"></div>{/if}
</div>
