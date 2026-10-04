<script lang="ts">
	import { browser } from '$app/environment';
	import { onDestroy } from 'svelte';
	import type { RawSource } from './Citations/reduceSources';
	import { DOCUMENT_SANDBOX, sanitizeDocumentHtml } from '$lib/utils/htmlDocument';
	import {
		transformDocumentCitations,
		bindDocumentCitationClicks
	} from '$lib/utils/documentCitations';
	import { documentPreviewThrottle } from '$lib/utils/documentPreview';

	let {
		content,
		title,
		done = true,
		sources = [],
		onSourceClick = () => {}
	}: {
		content: string;
		title: string;
		done?: boolean;
		sources?: RawSource[];
		onSourceClick?: (id: number) => void;
	} = $props();
	let width = $state(0);
	const pageWidth = (210 / 25.4) * 96;
	const pageHeight = (297 / 25.4) * 96;
	let height = $state(pageHeight);
	const scale = $derived(Math.min(1, width / pageWidth));
	let srcdoc = $state('');
	let container: HTMLDivElement;
	let scrollTop = 0;
	let scrollLeft = 0;
	let unbind: (() => void) | undefined;
	const preview = documentPreviewThrottle(
		(snapshot: { content: string; title: string; sources: RawSource[] }) => {
			scrollTop = container?.scrollTop ?? 0;
			scrollLeft = container?.scrollLeft ?? 0;
			srcdoc = transformDocumentCitations(
				sanitizeDocumentHtml(snapshot.content, snapshot.title),
				snapshot.sources,
				'preview'
			);
		}
	);
	$effect(() => {
		if (browser) preview.update({ content, title, sources }, done);
	});
	onDestroy(() => {
		preview.destroy();
		unbind?.();
	});

	function loadFrame(event: Event) {
		const frame = event.currentTarget as HTMLIFrameElement;
		height = Math.max(pageHeight, frame.contentDocument?.documentElement.scrollHeight ?? 0);
		unbind?.();
		if (frame.contentDocument)
			unbind = bindDocumentCitationClicks(frame.contentDocument, (id) => onSourceClick(id));
		container.scrollTop = scrollTop;
		container.scrollLeft = scrollLeft;
	}
</script>

<div
	class="w-full h-full min-h-0 shrink-0 overflow-auto bg-gray-100"
	bind:this={container}
	bind:clientWidth={width}
>
	<div style:height="{height * scale}px" class="mx-auto" style:width="{pageWidth * scale}px">
		<!-- Same-origin permits parent-controlled citation clicks and printing; scripts stay forbidden. -->
		<iframe
			{title}
			sandbox={DOCUMENT_SANDBOX}
			{srcdoc}
			onload={loadFrame}
			class="border-0 bg-white origin-top-left"
			style:width="{pageWidth}px"
			style:height="{height}px"
			style:transform="scale({scale})"
		></iframe>
	</div>
</div>
