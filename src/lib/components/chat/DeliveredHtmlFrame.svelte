<script lang="ts">
	// [Gradient] A delivered HTML page, interactive, in the document panel. The csp attribute makes Chromium
	// refuse any page the frame navigates to that does not accept the same policy.
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { getContext } from 'svelte';

	import {
		DELIVERED_HTML_CSP,
		DELIVERED_HTML_SANDBOX,
		deliveredHtmlDocument
	} from '$lib/utils/deliveredHtml';
	import { loadText, type DeliveredFile } from '$lib/utils/deliveredFile';

	export let file: DeliveredFile;
	export let chatId: string;

	const i18n = getContext<Writable<i18nType>>('i18n');
	// Set before srcdoc; the iframe types do not know the attribute yet.
	const policy = { csp: DELIVERED_HTML_CSP };

	let srcdoc: string | null = null;
	let failed = false;
	let loadedFor: DeliveredFile | undefined;

	$: if (file !== loadedFor) load(file);

	const load = async (wanted: DeliveredFile) => {
		loadedFor = wanted;
		srcdoc = null;
		failed = false;
		try {
			const loaded = deliveredHtmlDocument(await loadText(wanted, chatId));
			if (loadedFor === wanted) srcdoc = loaded;
		} catch (e) {
			console.error(e);
			failed = true;
		}
	};
</script>

{#if failed}
	<div class="m-auto text-xs text-red-500">
		{$i18n.t('Failed to load the preview. Download the file instead.')}
	</div>
{:else if srcdoc !== null}
	<iframe
		title={file.name}
		sandbox={DELIVERED_HTML_SANDBOX}
		{...policy}
		{srcdoc}
		class="block w-full h-full border-0 bg-white"
	></iframe>
{/if}
