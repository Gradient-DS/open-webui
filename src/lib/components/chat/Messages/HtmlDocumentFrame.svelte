<script lang="ts">
	import { browser } from '$app/environment';
	import { getContext, onDestroy, tick } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { DOCUMENT_SANDBOX, sanitizeDocumentHtml } from '$lib/utils/htmlDocument';
	import { documentPreviewThrottle } from '$lib/utils/documentPreview';
	import { fitPages, mmToPx, pagesOf, pageSize, pageStyle } from '$lib/utils/documentPages';
	import { fitZoom, ZOOM_STEPS } from '$lib/utils/documentZoom';

	const i18n = getContext<Writable<i18nType>>('i18n');

	let {
		content,
		title,
		page,
		done = true
	}: {
		content: string;
		title: string;
		page?: string;
		done?: boolean;
	} = $props();
	const size = $derived(pageSize(page));
	let width = $state(0);
	let panelHeight = $state(0);
	// The frame's measured content: its sheets with the gaps between them, or one flowing page, which may be
	// wider than its paper (fixed widths, margins).
	let contentWidth = $state(mmToPx(210));
	let height = $state(mmToPx(297));
	// How many page sections the document has; 0 for a document written as one flowing page.
	let sheets = $state(0);
	// null fits the pages to the panel inside its padding (px-6, pt-6 and the zoom bar's pb-16), never above
	// 100%: one page shows whole, more pages fit the width; a step is the user's own zoom.
	let zoom = $state<number | null>(null);
	const scale = $derived(
		zoom ??
			fitZoom(
				{ width: width - 48, height: panelHeight - 88 },
				{ width: contentWidth, height },
				sheets === 1 || (sheets === 0 && height <= mmToPx(size.height) * 1.05)
			)
	);
	let srcdoc = $state('');
	let container: HTMLDivElement;
	let scrollTop = 0;
	const preview = documentPreviewThrottle((snapshot: { content: string; title: string }) => {
		scrollTop = container?.scrollTop ?? 0;
		srcdoc = sanitizeDocumentHtml(snapshot.content, snapshot.title, {
			stripCitations: true,
			trustedStyle: pageStyle(snapshot.content, size, 'screen')
		});
	});
	$effect(() => {
		if (browser) preview.update({ content, title }, done);
	});
	onDestroy(() => {
		preview.destroy();
	});

	async function loadFrame(event: Event) {
		const doc = (event.currentTarget as HTMLIFrameElement).contentDocument;
		if (!doc) return;
		fitPages(doc, size);
		sheets = pagesOf(doc).length;
		contentWidth = Math.max(mmToPx(size.width), doc.documentElement.scrollWidth);
		height = Math.max(sheets ? 0 : mmToPx(size.height), doc.documentElement.scrollHeight);
		await tick();
		container.scrollTop = scrollTop;
	}

	function step(direction: 1 | -1) {
		const steps = direction > 0 ? ZOOM_STEPS : [...ZOOM_STEPS].reverse();
		zoom = steps.find((each) => direction * (each - scale) > 0.001) ?? steps.at(-1)!;
	}
</script>

<div class="relative w-full h-full min-h-0 shrink-0">
	<div
		class="w-full h-full overflow-auto bg-white px-6 pt-6 pb-16 dark:bg-gray-850"
		bind:this={container}
		bind:clientWidth={width}
		bind:clientHeight={panelHeight}
	>
		<!-- Sheets draw their own shadow inside the frame; a flowing page gets it here. -->
		<div
			style:height="{height * scale}px"
			class="mx-auto overflow-hidden {sheets
				? ''
				: 'shadow-[0_6px_28px_rgba(0,0,0,0.18)] ring-1 ring-black/5 dark:shadow-[0_6px_28px_rgba(0,0,0,0.65)] dark:ring-white/10'}"
			style:width="{contentWidth * scale}px"
		>
			<!-- Same-origin permits measuring the page; scripts stay forbidden. -->
			<iframe
				{title}
				sandbox={DOCUMENT_SANDBOX}
				{srcdoc}
				onload={loadFrame}
				class="block border-0 origin-top-left {sheets ? 'bg-transparent' : 'bg-white'}"
				style:width="{contentWidth}px"
				style:height="{height}px"
				style:transform="scale({scale})"
			></iframe>
		</div>
	</div>

	<div
		class="absolute bottom-3 left-1/2 z-10 flex -translate-x-1/2 items-center gap-0.5 rounded-lg border border-gray-200/60 bg-white/90 px-1 py-0.5 shadow-lg backdrop-blur-sm dark:border-gray-700/60 dark:bg-gray-850/90"
	>
		<button
			type="button"
			class="inline-flex h-7 min-w-7 shrink-0 items-center justify-center rounded-md p-1.5 text-gray-500 transition hover:bg-gray-100 disabled:opacity-40 dark:text-gray-400 dark:hover:bg-gray-800"
			onclick={() => step(-1)}
			disabled={scale <= ZOOM_STEPS[0]}
			aria-label={$i18n.t('Zoom out')}
		>
			<svg
				xmlns="http://www.w3.org/2000/svg"
				viewBox="0 0 20 20"
				fill="currentColor"
				class="size-3.5"
			>
				<path
					fill-rule="evenodd"
					d="M4 10a.75.75 0 0 1 .75-.75h10.5a.75.75 0 0 1 0 1.5H4.75A.75.75 0 0 1 4 10Z"
					clip-rule="evenodd"
				/>
			</svg>
		</button>
		<button
			type="button"
			class="h-7 min-w-12 shrink-0 rounded-md px-1.5 py-1 text-center text-[0.6875rem] font-normal tabular-nums text-gray-500 transition hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-800"
			onclick={() => (zoom = null)}
			aria-label={$i18n.t('Reset zoom')}
		>
			{Math.round(scale * 100)}%
		</button>
		<button
			type="button"
			class="inline-flex h-7 min-w-7 shrink-0 items-center justify-center rounded-md p-1.5 text-gray-500 transition hover:bg-gray-100 disabled:opacity-40 dark:text-gray-400 dark:hover:bg-gray-800"
			onclick={() => step(1)}
			disabled={scale >= ZOOM_STEPS.at(-1)!}
			aria-label={$i18n.t('Zoom in')}
		>
			<svg
				xmlns="http://www.w3.org/2000/svg"
				viewBox="0 0 20 20"
				fill="currentColor"
				class="size-3.5"
			>
				<path
					d="M10.75 4.75a.75.75 0 0 0-1.5 0v4.5h-4.5a.75.75 0 0 0 0 1.5h4.5v4.5a.75.75 0 0 0 1.5 0v-4.5h4.5a.75.75 0 0 0 0-1.5h-4.5v-4.5Z"
				/>
			</svg>
		</button>
	</div>
</div>
