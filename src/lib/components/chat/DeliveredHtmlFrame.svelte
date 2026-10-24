<script lang="ts">
	// [Gradient] A delivered HTML page in the document panel. Its scripts run only once the user starts them (once
	// per file until the app reloads), and the panel blanks the page the moment it starts to leave: a frame can always
	// navigate itself, and what it navigates to is not ours. The csp attribute makes Chromium refuse any page the
	// frame navigates to that does not accept the same policy.
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { getContext, onDestroy } from 'svelte';

	import {
		DELIVERED_HTML_CSP,
		SCRIPTED_SANDBOX,
		STATIC_SANDBOX,
		deliveredHtmlDocument,
		hasScripts,
		isLeaving,
		loadWatcher,
		runScripts,
		runsScripts
	} from '$lib/utils/deliveredHtml';
	import { deliveredFileName, loadText, type DeliveredFile } from '$lib/utils/deliveredFile';

	export let file: DeliveredFile;
	export let chatId: string;

	const i18n = getContext<Writable<i18nType>>('i18n');
	// Set before srcdoc; the iframe types do not know the attribute yet.
	const policy = { csp: DELIVERED_HTML_CSP };

	let content: string | null = null;
	let scripted = false;
	let running = false;
	let srcdoc: string | null = null;
	let frame: HTMLIFrameElement | undefined;
	let watcher = loadWatcher();
	let failed = false;
	let left = false;
	let loadedFor: DeliveredFile | undefined;

	$: if (file !== loadedFor) load(file);

	const show = () => {
		running = scripted && runsScripts(file.element_id);
		watcher = loadWatcher();
		srcdoc = content === null ? null : deliveredHtmlDocument(content, running);
	};

	const load = async (wanted: DeliveredFile) => {
		loadedFor = wanted;
		content = srcdoc = null;
		failed = left = false;
		try {
			const text = await loadText(wanted, chatId);
			if (loadedFor !== wanted) return;
			content = text;
			scripted = hasScripts(text);
			show();
		} catch (e) {
			console.error(e);
			failed = true;
		}
	};

	const start = () => {
		runScripts(file.element_id);
		show();
	};

	const leave = () => {
		left = true;
		srcdoc = null;
	};

	const onMessage = (event: MessageEvent) => {
		if (isLeaving(event, frame?.contentWindow)) leave();
	};
	window.addEventListener('message', onMessage);
	onDestroy(() => window.removeEventListener('message', onMessage));
</script>

{#if failed}
	<div class="m-auto text-xs text-red-500">
		{$i18n.t('Failed to load the preview. Download the file instead.')}
	</div>
{:else if left}
	<div class="m-auto max-w-sm px-4 text-center text-xs text-red-500" role="alert">
		{$i18n.t('This page tried to leave the panel and was stopped.')}
	</div>
{:else if srcdoc !== null}
	<div class="relative flex h-full w-full flex-col">
		{#if scripted && !running}
			<div
				class="flex shrink-0 items-center gap-2 border-b border-gray-100 bg-gray-50 px-3 py-1.5 text-xs text-gray-600 dark:border-gray-800 dark:bg-gray-900 dark:text-gray-300"
			>
				<span class="flex-1"
					>{$i18n.t('This page has scripts, which only run once you start them.')}</span
				>
				<button
					type="button"
					class="shrink-0 rounded-md bg-gray-900 px-2 py-1 font-medium text-white hover:bg-gray-800 dark:bg-white dark:text-gray-900 dark:hover:bg-gray-100"
					on:click={start}
				>
					{$i18n.t('Run interactive page')}
				</button>
			</div>
		{/if}
		{#key srcdoc}
			<iframe
				bind:this={frame}
				title={deliveredFileName(file)}
				sandbox={running ? SCRIPTED_SANDBOX : STATIC_SANDBOX}
				referrerpolicy="no-referrer"
				{...policy}
				{srcdoc}
				on:load={() => watcher.navigated() && leave()}
				class="block w-full flex-1 border-0 bg-white"
			></iframe>
		{/key}
	</div>
{/if}
