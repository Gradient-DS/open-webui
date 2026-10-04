<script lang="ts">
	import { browser } from '$app/environment';
	import { DOCUMENT_SANDBOX, sanitizeDocumentHtml } from '$lib/utils/htmlDocument';

	let { content, title }: { content: string; title: string } = $props();
	let width = $state(0);
	const pageWidth = (210 / 25.4) * 96;
	const pageHeight = (297 / 25.4) * 96;
	let height = $state(pageHeight);
	const scale = $derived(Math.min(1, width / pageWidth));
	const srcdoc = $derived(browser ? sanitizeDocumentHtml(content, title) : '');

	$effect(() => {
		if (srcdoc) height = pageHeight;
	});

	function resizeFrame(event: Event) {
		const frame = event.currentTarget as HTMLIFrameElement;
		height = Math.max(pageHeight, frame.contentDocument?.documentElement.scrollHeight ?? 0);
	}
</script>

<div class="w-full h-full overflow-auto bg-gray-100" bind:clientWidth={width}>
	<div style:height="{height * scale}px" class="mx-auto" style:width="{pageWidth * scale}px">
		<!-- [Gradient] Same-origin lets the parent print; scripts remain forbidden by sandbox and CSP. -->
		<iframe
			{title}
			sandbox={DOCUMENT_SANDBOX}
			{srcdoc}
			onload={resizeFrame}
			class="border-0 bg-white origin-top-left"
			style:width="{pageWidth}px"
			style:height="{height}px"
			style:transform="scale({scale})"
		></iframe>
	</div>
</div>
