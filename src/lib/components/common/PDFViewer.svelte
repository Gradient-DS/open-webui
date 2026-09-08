<script lang="ts">
	import { onMount, onDestroy, tick } from 'svelte';
	import pdfWorkerUrl from 'pdfjs-dist/build/pdf.worker.mjs?url';
	import panzoom, { type PanZoom } from 'panzoom';
	import { clampDocumentTargetPage } from '$lib/utils/documentPreview';
	import Spinner from './Spinner.svelte';
	import { matchItemsToNeedle, type PageTextItem } from '$lib/utils/citationMatch';
	import { pickAnchorRect, type HighlightRect } from '$lib/utils/citationRects';

	export let url: string | null = null;
	export let data: ArrayBuffer | Uint8Array | null = null;
	export let className = 'w-full h-[70vh]';
<<<<<<< HEAD
	// `highlightText` / `initialPage` / `highlightRects` seed the viewer's own
	// highlight state (see below) and are re-read whenever the parent changes
	// them. Inside a click handler the parent's values are still a tick stale,
	// so a caller switching snippets should call setHighlight(...) for the
	// immediate update and let the props follow.
	// Cited passage to highlight in the text layer (null/empty = no highlight).
	export let highlightText: string | null = null;
	// 1-indexed page to jump to when nothing matches the highlight text.
	export let initialPage: number | null = null;
	// Coordinate-based highlight rectangles. Takes precedence over
	// highlightText when non-empty — coordinates are exact, so no fuzzy text
	// matching is needed. Units: PDF points at scale 1, origin TOP-LEFT of the
	// page (i.e. y grows downward, matching pdf.js viewport space and most
	// OCR/layout tools; native bottom-left PDF user space must be flipped by
	// the caller: y_topleft = pageHeight - y_bottomleft). `page` is 1-indexed.
	export let highlightRects: HighlightRect[] | null = null;

	// --- Highlight state -------------------------------------------------------
	//
	// The props above only SEED this state; from then on setHighlight() owns it.
	// A component cannot keep a value it assigns to its own prop: the parent's
	// expression wins on the next flush, and inside a click handler that
	// expression is still a tick stale (the caller's `activeSnippetIdx` has not
	// propagated yet). Writing `initialPage` from setHighlight() therefore read
	// back as the PREVIOUS snippet's page, which sent the scroll to the wrong
	// place. Local state has no such owner conflict.
	let hlText = highlightText;
	let hlPage = initialPage;
	let hlRects = highlightRects;

	// True once page/text/bbox layers exist, i.e. once there is something to
	// highlight. Before that, render() applies whatever state is current.
	let layersRendered = false;

	/**
	 * Re-seed from the props when the parent supplies a new highlight (snippet
	 * switch, or props that resolve after the viewer was created) and re-apply.
	 */
	const _seedFromProps = (
		text: string | null,
		page: number | null,
		rects: HighlightRect[] | null
	) => {
		if (text === hlText && page === hlPage && rects === hlRects) return;
		hlText = text;
		hlPage = page;
		hlRects = rects;
		if (layersRendered) {
			_applyBestMatchHighlight();
		}
	};

	$: _seedFromProps(highlightText, initialPage, highlightRects);

	const HIGHLIGHT_CLASS = 'citation-highlight';
	// A page must accumulate at least this many matched characters to be
	// treated as the citation's location. Guards against latching onto a short
	// coincidental phrase (e.g. a title) when the cited text isn't really in
	// the PDF text layer (scanned pages) — in that case we page-jump instead.
	const MIN_MATCH_CHARS = 25;
=======
	export let targetPage: number | null = null;
	export let singlePage = false;
	export let itemLabel = 'Page';
	export let onPageChange: ((page: number) => void) | null = null;

	type PdfDocument = import('pdfjs-dist').PDFDocumentProxy;
	type PdfTextLayer = InstanceType<typeof import('pdfjs-dist').TextLayer>;
>>>>>>> upstream/main

	let outerContainer: HTMLDivElement;
	let sceneElement: HTMLDivElement;
	let loading = true;
	let error = '';
	let pdfDoc: PdfDocument | null = null;
	let pzInstance: PanZoom | null = null;
	let zoomLevel = 1;
	let rerenderTimer: ReturnType<typeof setTimeout> | null = null;
	let lastRenderedZoom = 1;
	let pageCount = 0;
	let renderedPage = 0;
	let activePage = 1;
	let loadToken = 0;
	let renderToken = 0;
	let scrollFrame: number | null = null;
	let mounted = false;
	let loadedSource: ArrayBuffer | Uint8Array | string | null = null;
	let wheelDelta = 0;
	let lastWheelNavigationAt = 0;
	const wheelNavigationThreshold = 80;
	const wheelNavigationCooldown = 450;
	const pageShortcutKeys = ['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'];

	$: selectedPage = singlePage ? (clampDocumentTargetPage(targetPage, pageCount) ?? 1) : activePage;

	// Keep a reference to TextLayer instances so we can update/cancel them
<<<<<<< HEAD
	let textLayerInstances: any[] = [];
	// Per-page text-layer container divs (index 0 == page 1). Lets us re-match
	// highlights against already-rendered spans without re-rendering canvases.
	let pageTextLayerDivs: HTMLElement[] = [];
	// Per-page bbox overlay divs (index 0 == page 1) and base page dimensions
	// in PDF points at scale 1. Rect highlights are positioned in percentages
	// of the page, so they track the page wrapper through zoom re-renders
	// without any recomputation.
	let bboxLayerDivs: HTMLElement[] = [];
	let pageBaseDims: { width: number; height: number }[] = [];

	// --- Citation highlighting -------------------------------------------------
	//
	// pdf.js 5.x TextLayer (see node_modules/pdfjs-dist/build/pdf.mjs #appendText)
	// renders one <span role="presentation"> per text item that has a non-empty
	// string, optionally wrapped in <span class="markedContent"> grouping spans
	// (display:contents) and interleaved with <br role="presentation"> for EOLs.
	// We therefore select leaf text spans only — `span:not(.markedContent)` —
	// which excludes the marked-content wrappers (selecting plain `span` would
	// double-count them) and the <br> elements (excluded by tag). Building the
	// matcher's PageTextItem[] directly from these rendered spans guarantees the
	// returned indices map exactly onto the spans we highlight: alignment is
	// correct by construction, independent of marked-content / EOL artefacts.

	const _leafTextSpans = (textLayerDiv: HTMLElement): HTMLSpanElement[] =>
		Array.from(textLayerDiv.querySelectorAll<HTMLSpanElement>('span:not(.markedContent)'));

	const _itemsFromSpans = (spans: HTMLSpanElement[]): PageTextItem[] =>
		spans.map((el, index) => ({ str: el.textContent ?? '', index }));

	interface PageMatch {
		spans: HTMLSpanElement[];
		hits: number[];
		/** Total matched characters — used to pick the strongest-matching page. */
		score: number;
	}

	/** Score a page's match against `needle` without mutating the DOM. */
	const _scorePage = (textLayerDiv: HTMLElement, needle: string): PageMatch => {
		const spans = _leafTextSpans(textLayerDiv);
		const hits = matchItemsToNeedle(_itemsFromSpans(spans), needle);
		const score = hits.reduce((sum, i) => sum + (spans[i]?.textContent?.length ?? 0), 0);
		return { spans, hits, score };
	};

	/** Remove every citation highlight from all rendered text layers. */
	const _clearHighlights = () => {
		let cleared = false;
		for (const textLayerDiv of pageTextLayerDivs) {
			for (const span of textLayerDiv.querySelectorAll(`span.${HIGHLIGHT_CLASS}`)) {
				span.classList.remove(HIGHLIGHT_CLASS);
				cleared = true;
			}
		}
		// Flush the class removal before the same spans are highlighted again, so
		// re-selecting the snippet you are already on replays the pulse instead of
		// leaving the animation mid-flight (nothing else marks the click as heard).
		if (cleared) void sceneElement?.offsetHeight;
	};

	/**
	 * Scroll the viewer's scroll container so the target element is centered.
	 * Scoped to `outerContainer` (computed scrollTop), not the window, so it
	 * cooperates with panzoom and does not move the whole page.
	 */
	// `align`: 'center' keeps the target mid-viewport; 'top' brings it near the
	// top with a small margin. Citations use 'top' so the START of the matched
	// passage (or the cited page) is at the top and reads downward, instead of
	// centering on the first matched line and pushing the rest below the fold.
	const _scrollContainerTo = (target: HTMLElement, align: 'center' | 'top' = 'center') => {
		if (!outerContainer) return;
		const containerRect = outerContainer.getBoundingClientRect();
		const targetRect = target.getBoundingClientRect();
		const margin =
			align === 'top'
				? Math.min(56, outerContainer.clientHeight * 0.12)
				: (outerContainer.clientHeight - targetRect.height) / 2;
		const delta = targetRect.top - containerRect.top - margin;
		outerContainer.scrollTop += delta;
	};

	const _scrollToFirstHighlight = (): boolean => {
		const first = sceneElement?.querySelector<HTMLElement>(`span.${HIGHLIGHT_CLASS}`);
		if (!first) return false;
		_scrollContainerTo(first, 'top');
		return true;
	};

	const _scrollToPage = (page: number) => {
		if (!page || page < 1) return;
		const wrapper = sceneElement?.querySelectorAll<HTMLElement>('.pdf-page-wrapper')[page - 1];
		if (wrapper) {
			_scrollContainerTo(wrapper, 'top');
		}
	};

	/**
	 * Draw coordinate rectangles into the per-page bbox overlays and scroll to
	 * the one that anchors the citation (see `pickAnchorRect` — the cited page
	 * wins over the list's own order). Always clears previous rects (so
	 * switching to a snippet without geometry removes stale boxes). Returns
	 * whether anything was drawn.
	 */
	const _applyRectHighlights = (): boolean => {
		for (const layer of bboxLayerDivs) {
			layer.innerHTML = '';
		}
		const rects = hlRects ?? [];
		// Only rects that actually landed on a rendered page can be scrolled to,
		// so the anchor is chosen among these rather than among the input list.
		const drawn: { rect: HighlightRect; el: HTMLElement }[] = [];
		for (const rect of rects) {
			const layer = bboxLayerDivs[rect.page - 1];
			const dims = pageBaseDims[rect.page - 1];
			if (!layer || !dims) continue;
			// Clamp to the page box — geometry from OCR/layout tools can
			// overshoot the media box by a point or two.
			const x0 = Math.max(0, Math.min(rect.x0, dims.width));
			const y0 = Math.max(0, Math.min(rect.y0, dims.height));
			const x1 = Math.max(0, Math.min(rect.x1, dims.width));
			const y1 = Math.max(0, Math.min(rect.y1, dims.height));
			if (x1 <= x0 || y1 <= y0) continue;
			const div = document.createElement('div');
			div.className = 'bbox-highlight';
			div.style.left = `${(x0 / dims.width) * 100}%`;
			div.style.top = `${(y0 / dims.height) * 100}%`;
			div.style.width = `${((x1 - x0) / dims.width) * 100}%`;
			div.style.height = `${((y1 - y0) / dims.height) * 100}%`;
			layer.appendChild(div);
			drawn.push({ rect, el: div });
		}
		if (drawn.length === 0) return false;
		const anchor = pickAnchorRect(
			drawn.map((d) => d.rect),
			hlPage
		);
		const target = drawn.find((d) => d.rect === anchor)?.el ?? drawn[0].el;
		_scrollContainerTo(target, 'top');
		return true;
	};

	/**
	 * Highlight only the STRONGEST-matching page and scroll to it. Chunk text
	 * fragments (titles, headers, repeated phrases) often appear on several
	 * pages; highlighting every match and scrolling to the first latched onto
	 * coincidental matches on unrelated pages. Scoring by matched characters and
	 * keeping only the best page lands on the cited passage's real location.
	 * Fallback chain: exact bbox rects -> best-page text highlight+scroll ->
	 * cited-page jump -> nothing. Clears prior highlights first.
	 */
	const _applyBestMatchHighlight = () => {
		_clearHighlights();
		if (_applyRectHighlights()) return;
		const needle = hlText?.trim();
		if (needle) {
			let best: PageMatch | null = null;
			for (const textLayerDiv of pageTextLayerDivs) {
				const page = _scorePage(textLayerDiv, needle);
				if (page.score > (best?.score ?? 0)) {
					best = page;
				}
			}
			if (best && best.score >= MIN_MATCH_CHARS) {
				for (const i of best.hits) {
					best.spans[i]?.classList.add(HIGHLIGHT_CLASS);
				}
				_scrollToFirstHighlight();
				return;
			}
		}
		if (hlPage) {
			_scrollToPage(hlPage);
		}
	};

	/**
	 * Update the highlighted passage on the already-rendered text layers without
	 * re-rendering the PDF canvases, then re-scroll. Cheap snippet switching.
	 * Safe to call before the first render completes: the values are held as
	 * local state and honored when render runs.
	 */
	export function setHighlight(
		text: string | null,
		page: number | null,
		rects: HighlightRect[] | null = null
	) {
		hlText = text;
		hlPage = page;
		hlRects = rects;
		_applyBestMatchHighlight();
	}
=======
	let textLayerInstances: PdfTextLayer[] = [];

	const copyPdfData = (pdfData: ArrayBuffer | Uint8Array) =>
		pdfData instanceof Uint8Array ? pdfData.slice() : pdfData.slice(0);

	const cancelTextLayers = () => {
		for (const tl of textLayerInstances) {
			try {
				tl.cancel();
			} catch {
				// Text layers can already be resolved or canceled during rerenders.
			}
		}
		textLayerInstances = [];
	};
>>>>>>> upstream/main

	const initPanzoom = () => {
		if (pzInstance) {
			pzInstance.dispose();
		}
		if (sceneElement) {
			pzInstance = panzoom(sceneElement, {
				bounds: true,
				boundsPadding: 0.1,
				zoomSpeed: 0.065,
				beforeWheel: (e) => {
					// Only zoom on pinch (ctrlKey / metaKey); let normal scroll pass through
					if (!e.ctrlKey && !e.metaKey) {
						return true; // returning true cancels the panzoom wheel handling
					}
					return false;
				},
				beforeMouseDown: (e) => {
					// Only allow drag-to-pan when zoomed in (not at default scale)
					if ((e?.target as HTMLElement | null)?.closest?.('.textLayer')) {
						return true;
					}
					const transform = pzInstance?.getTransform();
					if (transform && Math.abs(transform.scale - 1) < 0.01) {
						return true; // cancel panzoom mouse handling at 1x — allow text selection / normal interaction
					}
					return false;
				}
			});
			pzInstance.on('zoom', () => {
				zoomLevel = pzInstance?.getTransform()?.scale ?? 1;
				// Debounced re-render at new resolution so text stays crisp
				if (rerenderTimer) clearTimeout(rerenderTimer);
				rerenderTimer = setTimeout(() => {
					if (Math.abs(zoomLevel - lastRenderedZoom) > 0.05) {
						rerenderPages(zoomLevel);
					}
				}, 300);
			});
		}
	};

	const zoomIn = () => {
		if (!pzInstance || !outerContainer) return;
		const cx = outerContainer.clientWidth / 2;
		const cy = outerContainer.clientHeight / 2;
		pzInstance.zoomTo(cx, cy, 1.25); // +25%
		zoomLevel = pzInstance.getTransform().scale;
	};

	const zoomOut = () => {
		if (!pzInstance || !outerContainer) return;
		const cx = outerContainer.clientWidth / 2;
		const cy = outerContainer.clientHeight / 2;
		pzInstance.zoomTo(cx, cy, 0.8); // -20% (inverse of 1.25)
		zoomLevel = pzInstance.getTransform().scale;
	};

	export const resetView = () => {
		if (pzInstance) {
			pzInstance.moveTo(0, 0);
			pzInstance.zoomAbs(0, 0, 1);
			zoomLevel = 1;
			rerenderPages(1);
		}
	};

	export const scrollToPage = async (page: number | null) => {
		targetPage = page;
		if (singlePage) {
			if (targetPage) onPageChange?.(targetPage);
		} else {
			await scrollToTargetPage();
		}
	};

	const selectPage = async (page: number) => {
		if (!pdfDoc) return;
		const nextPage = clampDocumentTargetPage(page, pdfDoc.numPages);
		if (!nextPage || nextPage === selectedPage) return;

		targetPage = nextPage;
		activePage = nextPage;
		onPageChange?.(nextPage);
		if (!singlePage) await scrollToTargetPage();
	};

	const scrollToTargetPage = async () => {
		if (!outerContainer || !sceneElement || !pdfDoc) return;
		const page = clampDocumentTargetPage(targetPage, pdfDoc.numPages);
		if (!page) return;

		if (singlePage) return;

		await tick();
		const pageWrapper = sceneElement.querySelectorAll('.pdf-page-wrapper')[page - 1] as
			| HTMLElement
			| undefined;
		pageWrapper?.scrollIntoView({ block: 'start' });
		activePage = page;
		onPageChange?.(page);
	};

	const syncVisiblePage = () => {
		scrollFrame = null;
		if (singlePage || !outerContainer || !sceneElement || !pdfDoc) return;

		const marker = outerContainer.getBoundingClientRect().top + outerContainer.clientHeight * 0.35;
		let bestPage = activePage;
		let bestDistance = Number.POSITIVE_INFINITY;

		for (const wrapper of sceneElement.querySelectorAll('.pdf-page-wrapper')) {
			const el = wrapper as HTMLElement;
			const page = Number(el.dataset.pageNumber);
			if (!page) continue;

			const rect = el.getBoundingClientRect();
			const distance =
				marker < rect.top ? rect.top - marker : marker > rect.bottom ? marker - rect.bottom : 0;
			if (distance < bestDistance) {
				bestDistance = distance;
				bestPage = page;
			}
		}

		if (bestPage !== activePage) {
			activePage = bestPage;
			onPageChange?.(bestPage);
		}
	};

	const handleScroll = () => {
		if (singlePage || scrollFrame !== null) return;
		scrollFrame = requestAnimationFrame(syncVisiblePage);
	};

	// Re-render existing canvases at a new zoom level (preserves panzoom transform)
	const rerenderPages = async (forZoom: number) => {
		if (!pdfDoc || !sceneElement) return;
		const pdfjs = await import('pdfjs-dist');
		const dpr = window.devicePixelRatio || 1;

		const pageWrappers = sceneElement.querySelectorAll('.pdf-page-wrapper');

<<<<<<< HEAD
		// Cancel old text layers
		for (const tl of textLayerInstances) {
			try {
				tl.cancel();
			} catch (_) {}
		}
		textLayerInstances = [];
		pageTextLayerDivs = [];
=======
		cancelTextLayers();
>>>>>>> upstream/main

		for (let i = 0; i < pageWrappers.length; i++) {
			const page = await pdfDoc.getPage(singlePage ? selectedPage : i + 1);
			const viewport = page.getViewport({ scale: 1 });
			const cssScale = getCssScale(viewport);
			const renderScale = cssScale * forZoom * dpr;
			const scaledViewport = page.getViewport({ scale: renderScale });
			const cssViewport = page.getViewport({ scale: cssScale });

			const wrapper = pageWrappers[i] as HTMLElement;
			// Update the CSS custom property so textLayer dimensions resolve correctly
			wrapper.style.setProperty('--scale-factor', String(cssViewport.scale));

			const canvas = wrapper.querySelector('canvas')!;
			canvas.width = scaledViewport.width;
			canvas.height = scaledViewport.height;

			const ctx = canvas.getContext('2d');
			if (ctx) {
				await page.render({ canvas, canvasContext: ctx, viewport: scaledViewport }).promise;
			}

			// Rebuild text layer
			const textLayerDiv = wrapper.querySelector('.textLayer') as HTMLElement;
			if (textLayerDiv) {
				textLayerDiv.innerHTML = '';

				const textContent = await page.getTextContent();
				const textLayer = new pdfjs.TextLayer({
					textContentSource: textContent,
					container: textLayerDiv,
					viewport: cssViewport
				});
				await textLayer.render();
				textLayerInstances.push(textLayer);
				pageTextLayerDivs.push(textLayerDiv);
			}
		}
		lastRenderedZoom = forZoom;
		_applyBestMatchHighlight();
	};

	const getCssScale = (viewport: { width: number; height: number }) => {
		if (!singlePage) return (outerContainer?.clientWidth || 800) / viewport.width;

		const availableWidth = Math.max(320, (outerContainer?.clientWidth || 800) - 64);
		const availableHeight = Math.max(220, (outerContainer?.clientHeight || 600) - 64);
		return Math.min(1, availableWidth / viewport.width, availableHeight / viewport.height);
	};

	const renderAllPages = async () => {
		if (!pdfDoc || !sceneElement) return;
		const token = ++renderToken;

		// Clear previous content
		sceneElement.innerHTML = '';

<<<<<<< HEAD
		// Cancel old text layers
		for (const tl of textLayerInstances) {
			try {
				tl.cancel();
			} catch (_) {}
		}
		textLayerInstances = [];
		pageTextLayerDivs = [];
		bboxLayerDivs = [];
		pageBaseDims = [];

		const pdfjs = await import('pdfjs-dist');
		const dpr = window.devicePixelRatio || 1;
		for (let i = 1; i <= pdfDoc.numPages; i++) {
=======
		cancelTextLayers();

		const pdfjs = await import('pdfjs-dist');
		const dpr = window.devicePixelRatio || 1;
		const wrappers: HTMLElement[] = [];
		const firstPage = singlePage ? selectedPage : 1;
		const lastPage = singlePage ? selectedPage : pdfDoc.numPages;

		for (let i = firstPage; i <= lastPage; i++) {
>>>>>>> upstream/main
			const page = await pdfDoc.getPage(i);
			if (token !== renderToken) return;
			const viewport = page.getViewport({ scale: 1 });

			// Scale to fit container width
			const cssScale = getCssScale(viewport);
			const renderScale = cssScale * dpr;
			const scaledViewport = page.getViewport({ scale: renderScale });
			const cssViewport = page.getViewport({ scale: cssScale });

			// Create page wrapper (positioned container for canvas + text layer)
			const wrapper = document.createElement('div');
			wrapper.className = 'pdf-page-wrapper';
			wrapper.dataset.pageNumber = String(i);
			wrapper.style.position = 'relative';
			wrapper.style.width = `${Math.round(cssScale * viewport.width)}px`;
			wrapper.style.height = `${Math.round(cssScale * viewport.height)}px`;
			wrapper.style.display = 'block';
			// pdfjs TextLayer uses --total-scale-factor (= --scale-factor * --user-unit)
			// to position/size text spans. We must set --scale-factor so the calc resolves.
			wrapper.style.setProperty('--scale-factor', String(cssViewport.scale));

			if (i > 1) {
				wrapper.style.marginTop = '4px';
			}

			// Create canvas
			const canvas = document.createElement('canvas');
			canvas.width = scaledViewport.width;
			canvas.height = scaledViewport.height;
			// CSS size stays at the CSS-pixel dimensions for layout
			canvas.style.width = `${Math.round(cssScale * viewport.width)}px`;
			canvas.style.height = `${Math.round(cssScale * viewport.height)}px`;
			canvas.style.display = 'block';
			wrapper.appendChild(canvas);

			const ctx = canvas.getContext('2d');
			if (!ctx) continue;

			await page.render({
				canvas,
				canvasContext: ctx,
				viewport: scaledViewport
			}).promise;
			if (token !== renderToken) return;

			// Create text layer overlay — pdfjs setLayerDimensions handles its sizing
			const textLayerDiv = document.createElement('div');
			textLayerDiv.className = 'textLayer';
			wrapper.appendChild(textLayerDiv);

			const textContent = await page.getTextContent();
			const textLayer = new pdfjs.TextLayer({
				textContentSource: textContent,
				container: textLayerDiv,
				viewport: cssViewport
			});
			await textLayer.render();
			if (token !== renderToken) return;
			textLayerInstances.push(textLayer);
			pageTextLayerDivs.push(textLayerDiv);

			// Bbox overlay — sits above the text layer but is click-transparent
			// so selection/search still hit the text spans underneath.
			const bboxLayerDiv = document.createElement('div');
			bboxLayerDiv.className = 'bboxLayer';
			wrapper.appendChild(bboxLayerDiv);
			bboxLayerDivs.push(bboxLayerDiv);
			pageBaseDims.push({ width: viewport.width, height: viewport.height });

			wrappers.push(wrapper);
		}

		sceneElement.replaceChildren(...wrappers);
		lastRenderedZoom = 1;
<<<<<<< HEAD
		layersRendered = true;
		initPanzoom();
		_applyBestMatchHighlight();
=======
		renderedPage = singlePage ? selectedPage : 0;
		initPanzoom();
		await scrollToTargetPage();
		syncVisiblePage();
	};

	const handleWheel = (e: WheelEvent) => {
		if (!singlePage) return;
		if (e.ctrlKey || e.metaKey) return;

		const transform = pzInstance?.getTransform();
		if (transform && Math.abs(transform.scale - 1) >= 0.01) {
			e.preventDefault();
			pzInstance?.moveBy(-e.deltaX, -e.deltaY, false);
			zoomLevel = pzInstance?.getTransform()?.scale ?? 1;
			return;
		}

		e.preventDefault();
		if (pageCount <= 1) return;

		const multiplier = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? outerContainer.clientHeight : 1;
		const dominantDelta = Math.abs(e.deltaY) >= Math.abs(e.deltaX) ? e.deltaY : e.deltaX;
		wheelDelta += dominantDelta * multiplier;

		const now = Date.now();
		if (Math.abs(wheelDelta) < wheelNavigationThreshold) return;
		if (now - lastWheelNavigationAt < wheelNavigationCooldown) {
			wheelDelta = 0;
			return;
		}

		lastWheelNavigationAt = now;
		void selectPage(selectedPage + (wheelDelta > 0 ? 1 : -1));
		wheelDelta = 0;
	};

	const handleKeyDown = (e: KeyboardEvent) => {
		if (
			!singlePage ||
			e.defaultPrevented ||
			e.altKey ||
			e.ctrlKey ||
			e.metaKey ||
			pageCount <= 1 ||
			!pageShortcutKeys.includes(e.key)
		) {
			return;
		}

		e.preventDefault();
		void selectPage(selectedPage + (e.key === 'ArrowUp' || e.key === 'ArrowLeft' ? -1 : 1));
	};

	const focusViewer = () => {
		if (!outerContainer?.contains(document.activeElement)) outerContainer?.focus();
>>>>>>> upstream/main
	};

	const loadPdf = async () => {
		if (!url && !data) return;

		const source = data ?? url;
		if (source === loadedSource && pdfDoc) return;
		const token = ++loadToken;
		loadedSource = source;
		loading = true;
		error = '';
		renderedPage = 0;
		pageCount = 0;
		pzInstance?.dispose();
		cancelTextLayers();
		pdfDoc?.destroy();
		pdfDoc = null;

		try {
			const pdfjs = await import('pdfjs-dist');
			pdfjs.GlobalWorkerOptions.workerSrc = pdfWorkerUrl;

			let pdfData: ArrayBuffer | Uint8Array;
			if (data) {
				pdfData = copyPdfData(data);
			} else {
				// Authenticate like the rest of the app: send the Bearer token from
				// localStorage. The /files/{id}/content endpoint needs auth; relying
				// on the cookie alone (credentials:'include') 401s whenever the cookie
				// isn't sent (OAuth login, non-localhost host, SameSite/expiry) —
				// which surfaces as "Failed to load PDF". credentials kept as a fallback.
				const authToken = localStorage.getItem('token');
				const res = await fetch(url!, {
					credentials: 'include',
					headers: authToken ? { authorization: `Bearer ${authToken}` } : {}
				});
				if (!res.ok) throw new Error(`HTTP ${res.status}`);
				pdfData = await res.arrayBuffer();
			}
			pdfDoc = await pdfjs.getDocument({ data: pdfData }).promise;
			if (token !== loadToken) return;
			pageCount = pdfDoc.numPages;
			activePage = clampDocumentTargetPage(targetPage, pageCount) ?? 1;
			targetPage = clampDocumentTargetPage(targetPage, pageCount) ?? 1;
			await renderAllPages();
		} catch (e) {
			if (token === loadToken) {
				console.error('PDF render error:', e);
				error = 'Failed to load PDF.';
			}
		} finally {
			if (token === loadToken) loading = false;
		}
	};

	onMount(() => {
		mounted = true;
		loadPdf();
	});

	$: if (mounted && (data || url)) {
		void loadPdf();
	}

	$: if (!loading && pdfDoc && singlePage && targetPage && selectedPage !== renderedPage) {
		void renderAllPages();
	}

	$: if (!loading && pdfDoc && !singlePage && targetPage) {
		void scrollToTargetPage();
	}

	onDestroy(() => {
		loadToken++;
		renderToken++;
		if (scrollFrame !== null) cancelAnimationFrame(scrollFrame);
		if (rerenderTimer) clearTimeout(rerenderTimer);
		pzInstance?.dispose();
		cancelTextLayers();
		if (pdfDoc) {
			pdfDoc.destroy();
			pdfDoc = null;
		}
	});
</script>

<div class="relative {className}">
	{#if loading}
		<div class="absolute inset-0 flex items-center justify-center">
			<Spinner className="size-5" />
		</div>
	{:else if error}
		<div class="absolute inset-0 flex items-center justify-center text-sm text-red-500">
			{error}
		</div>
	{/if}

	<div
		class={singlePage
			? 'overflow-hidden h-full flex items-center justify-center overscroll-contain'
			: 'overflow-y-auto h-full'}
		bind:this={outerContainer}
		role="application"
		aria-label={`${itemLabel} viewer`}
		tabindex="0"
		on:scroll={handleScroll}
		on:wheel|nonpassive={handleWheel}
		on:pointerdown={focusViewer}
		on:keydown={handleKeyDown}
	>
		<div bind:this={sceneElement} class={singlePage ? '' : 'w-full'}></div>
	</div>

	{#if !error && pdfDoc}
		<div
			class="absolute bottom-3 left-1/2 -translate-x-1/2 z-10 flex items-center gap-0.5 rounded-lg bg-white/90 dark:bg-gray-850/90 backdrop-blur-sm shadow-lg border border-gray-200/60 dark:border-gray-700/60 px-1 py-0.5"
		>
			{#if singlePage}
				<button
					type="button"
					class="shrink-0 min-w-7 h-7 inline-flex items-center justify-center p-1.5 rounded-md hover:bg-gray-100 dark:hover:bg-gray-800 transition text-gray-500 dark:text-gray-400 disabled:opacity-30"
					disabled={selectedPage === 1}
					on:click={() => selectPage(selectedPage - 1)}
					aria-label={`Previous ${itemLabel.toLowerCase()}`}
				>
					<svg
						xmlns="http://www.w3.org/2000/svg"
						viewBox="0 0 20 20"
						fill="currentColor"
						class="size-3.5"
					>
						<path
							fill-rule="evenodd"
							d="M11.78 5.22a.75.75 0 0 1 0 1.06L8.06 10l3.72 3.72a.75.75 0 1 1-1.06 1.06l-4.25-4.25a.75.75 0 0 1 0-1.06l4.25-4.25a.75.75 0 0 1 1.06 0Z"
							clip-rule="evenodd"
						/>
					</svg>
				</button>
				<span
					class="shrink-0 min-w-12 text-center text-[0.6875rem] text-gray-500 dark:text-gray-400 tabular-nums"
					>{selectedPage} / {pageCount}</span
				>
				<button
					type="button"
					class="shrink-0 min-w-7 h-7 inline-flex items-center justify-center p-1.5 rounded-md hover:bg-gray-100 dark:hover:bg-gray-800 transition text-gray-500 dark:text-gray-400 disabled:opacity-30"
					disabled={selectedPage === pageCount}
					on:click={() => selectPage(selectedPage + 1)}
					aria-label={`Next ${itemLabel.toLowerCase()}`}
				>
					<svg
						xmlns="http://www.w3.org/2000/svg"
						viewBox="0 0 20 20"
						fill="currentColor"
						class="size-3.5"
					>
						<path
							fill-rule="evenodd"
							d="M8.22 5.22a.75.75 0 0 1 1.06 0l4.25 4.25a.75.75 0 0 1 0 1.06l-4.25 4.25a.75.75 0 0 1-1.06-1.06L11.94 10 8.22 6.28a.75.75 0 0 1 0-1.06Z"
							clip-rule="evenodd"
						/>
					</svg>
				</button>
			{/if}
			<!-- Pinch covers in/out on coarse pointers; reset has no gesture, so it stays -->
			<button
				type="button"
				class="shrink-0 min-w-7 h-7 inline-flex items-center justify-center p-1.5 rounded-md hover:bg-gray-100 dark:hover:bg-gray-800 transition text-gray-500 dark:text-gray-400 pointer-coarse:hidden"
				on:click={zoomOut}
				aria-label="Zoom out"
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
				class="shrink-0 min-w-12 h-7 px-1.5 py-1 text-center text-[0.6875rem] font-normal text-gray-500 dark:text-gray-400 rounded-md hover:bg-gray-100 dark:hover:bg-gray-800 transition tabular-nums"
				on:click={resetView}
				aria-label="Reset zoom"
			>
				{Math.round(zoomLevel * 100)}%
			</button>
			<button
				type="button"
				class="shrink-0 min-w-7 h-7 inline-flex items-center justify-center p-1.5 rounded-md hover:bg-gray-100 dark:hover:bg-gray-800 transition text-gray-500 dark:text-gray-400 pointer-coarse:hidden"
				on:click={zoomIn}
				aria-label="Zoom in"
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
	{/if}
</div>

<style>
	/*
	 * Minimal textLayer styles extracted from pdfjs-dist/web/pdf_viewer.css.
	 * These ensure the invisible text spans are positioned exactly over the
	 * rendered canvas so that browser-native Ctrl+F search and text selection
	 * work correctly.
	 */
	:global(.textLayer) {
		position: absolute;
		text-align: initial;
		inset: 0;
		overflow: clip;
		opacity: 1;
		line-height: 1;
		-webkit-text-size-adjust: none;
		-moz-text-size-adjust: none;
		text-size-adjust: none;
		forced-color-adjust: none;
		transform-origin: 0 0;
		caret-color: CanvasText;
		z-index: 0;
	}

	:global(.textLayer :is(span, br)) {
		color: transparent;
		position: absolute;
		white-space: pre;
		cursor: text;
		transform-origin: 0% 0%;
	}

	:global(.textLayer) {
		/* --total-scale-factor is derived from --scale-factor (set on the wrapper)
		   and --user-unit (defaults to 1). This mirrors the official pdf_viewer.css. */
		--user-unit: 1;
		--total-scale-factor: calc(var(--scale-factor) * var(--user-unit));
		--min-font-size: 1;
		--text-scale-factor: calc(var(--total-scale-factor) * var(--min-font-size));
		--min-font-size-inv: calc(1 / var(--min-font-size));
	}

	:global(.textLayer > :not(.markedContent)),
	:global(.textLayer .markedContent span:not(.markedContent)) {
		z-index: 1;
		--font-height: 0;
		font-size: calc(var(--text-scale-factor) * var(--font-height));
		--scale-x: 1;
		--rotate: 0deg;
		transform: rotate(var(--rotate)) scaleX(var(--scale-x)) scale(var(--min-font-size-inv));
	}

	:global(.textLayer .markedContent) {
		display: contents;
	}

	:global(.textLayer span[role='img']) {
		-webkit-user-select: none;
		-moz-user-select: none;
		user-select: none;
		cursor: default;
	}

	/* Selection highlight color */
	:global(.textLayer ::-moz-selection) {
		background: rgba(0, 0, 255, 0.25);
	}

	:global(.textLayer ::selection) {
		background: rgba(0, 0, 255, 0.25);
	}

	:global(.textLayer br::-moz-selection) {
		background: transparent;
	}

	:global(.textLayer br::selection) {
		background: transparent;
	}

	:global(.textLayer .endOfContent) {
		display: block;
		position: absolute;
		inset: 100% 0 0;
		z-index: 0;
		cursor: default;
		-webkit-user-select: none;
		-moz-user-select: none;
		user-select: none;
	}

	:global(.textLayer.selecting .endOfContent) {
		top: 0;
	}

	/* Citation highlight — applied to leaf text spans by setHighlight().
	   :global is required because pdf.js (not Svelte) creates these spans. */
	:global(.textLayer span.citation-highlight) {
		background: rgba(250, 204, 21, 0.45); /* amber-300 */
		border-radius: 2px;
		mix-blend-mode: multiply;
		animation: citation-pulse 900ms ease-out 1;
	}
	:global(.dark .textLayer span.citation-highlight) {
		mix-blend-mode: screen;
		background: rgba(250, 204, 21, 0.35);
	}

	/* Coordinate (bbox) highlight overlay — divs created by
	   _applyRectHighlights(), positioned in % of the page wrapper so they
	   scale with zoom re-renders for free. :global because JS-created. */
	:global(.pdf-page-wrapper .bboxLayer) {
		position: absolute;
		inset: 0;
		pointer-events: none;
		z-index: 2;
	}
	:global(.bboxLayer .bbox-highlight) {
		position: absolute;
		background: rgba(250, 204, 21, 0.3); /* amber-300, matches text highlight */
		outline: 1.5px solid rgba(245, 158, 11, 0.85); /* amber-500 */
		border-radius: 2px;
		mix-blend-mode: multiply;
		animation: citation-pulse 900ms ease-out 1;
	}
	:global(.dark .bboxLayer .bbox-highlight) {
		mix-blend-mode: screen;
		background: rgba(250, 204, 21, 0.25);
	}

	/* Highlights are redrawn on every selection, so this plays once each time a
	   snippet is picked — including re-picking the snippet the viewer is already
	   parked on, where there is no scrolling to show the click landed.
	   `-global-` keeps the name unscoped for the :global rules above. */
	@keyframes -global-citation-pulse {
		0% {
			background: rgba(250, 204, 21, 0.85);
			outline-color: rgb(245, 158, 11);
		}
		60% {
			background: rgba(250, 204, 21, 0.85);
			outline-color: rgb(245, 158, 11);
		}
	}

	@media (prefers-reduced-motion: reduce) {
		:global(.textLayer span.citation-highlight),
		:global(.bboxLayer .bbox-highlight) {
			animation: none;
		}
	}
</style>
