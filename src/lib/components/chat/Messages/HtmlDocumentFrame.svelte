<script lang="ts">
	import { browser } from '$app/environment';
	import { onDestroy, tick } from 'svelte';
	import { DOCUMENT_SANDBOX, sanitizeDocumentHtml } from '$lib/utils/htmlDocument';
	import { documentPreviewThrottle } from '$lib/utils/documentPreview';

	let {
		content,
		title,
		done = true
	}: {
		content: string;
		title: string;
		done?: boolean;
	} = $props();
	let width = $state(0);
	const pageWidth = (210 / 25.4) * 96;
	const pageHeight = (297 / 25.4) * 96;
	let height = $state(pageHeight);
	const scale = $derived(width / pageWidth);
	let srcdoc = $state('');
	let container: HTMLDivElement;
	let scrollTop = 0;
	const preview = documentPreviewThrottle((snapshot: { content: string; title: string }) => {
		scrollTop = container?.scrollTop ?? 0;
		srcdoc = sanitizeDocumentHtml(snapshot.content, snapshot.title, { stripCitations: true });
	});
	$effect(() => {
		if (browser) preview.update({ content, title }, done);
	});
	onDestroy(() => {
		preview.destroy();
	});

	async function loadFrame(event: Event) {
		const frame = event.currentTarget as HTMLIFrameElement;
		height = Math.max(pageHeight, frame.contentDocument?.documentElement.scrollHeight ?? 0);
		await tick();
		container.scrollTop = scrollTop;
	}
</script>

<div
	class="w-full h-full min-h-0 shrink-0 overflow-x-hidden overflow-y-auto bg-gray-100"
	bind:this={container}
	bind:clientWidth={width}
>
	<div
		style:height="{height * scale}px"
		class="mx-auto overflow-hidden"
		style:width="{pageWidth * scale}px"
	>
		<!-- Same-origin permits measuring the page; scripts stay forbidden. -->
		<iframe
			{title}
			sandbox={DOCUMENT_SANDBOX}
			{srcdoc}
			onload={loadFrame}
			class="block border-0 bg-white origin-top-left"
			style:width="{pageWidth}px"
			style:height="{height}px"
			style:transform="scale({scale})"
		></iframe>
	</div>
</div>
