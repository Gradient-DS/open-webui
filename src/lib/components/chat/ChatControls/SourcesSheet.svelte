<script context="module" lang="ts">
	export { sourcesSheetClass } from './sourcesSheet';
</script>

<script lang="ts">
	import { getContext } from 'svelte';
	import type { i18n as I18n } from 'i18next';
	import type { Readable } from 'svelte/store';
	import { sheetRelease } from './sourcesSheet';
	const i18n = getContext<Readable<I18n>>('i18n');
	export let expanded = false;
	export let onClose: () => void = () => {};

	// Dragging the handle resizes the sheet (the drawer panel the handle sits in);
	// on release it snaps to full height, back to its normal height, or closes.
	let handle: HTMLButtonElement;
	let sheet: HTMLElement | null = null;
	let startY = 0;
	let startHeight = 0;
	let dragged = false;

	const onPointerDown = (e: PointerEvent) => {
		sheet = handle.parentElement?.closest('.modal > div') ?? null;
		if (!sheet) return;
		startY = e.clientY;
		startHeight = sheet.getBoundingClientRect().height;
		dragged = false;
		handle.setPointerCapture(e.pointerId);
	};

	const onPointerMove = (e: PointerEvent) => {
		if (!sheet || !handle.hasPointerCapture(e.pointerId)) return;
		const dy = e.clientY - startY;
		if (Math.abs(dy) > 4) dragged = true;
		if (!dragged) return;
		sheet.style.transition = 'none';
		sheet.style.height = `${Math.min(window.innerHeight, Math.max(0, startHeight - dy))}px`;
	};

	const onPointerUp = (e: PointerEvent) => {
		if (!sheet || !handle.hasPointerCapture(e.pointerId)) return;
		handle.releasePointerCapture(e.pointerId);
		const height = sheet.getBoundingClientRect().height;
		sheet.style.height = '';
		sheet.style.transition = '';
		sheet = null;
		if (!dragged) return;
		const next = sheetRelease(height, window.innerHeight);
		if (next === 'close') onClose();
		else expanded = next === 'expanded';
	};
</script>

<button
	bind:this={handle}
	class="shrink-0 w-full flex justify-center py-3 touch-none cursor-grab"
	aria-label={$i18n.t('Toggle citation panel height')}
	aria-expanded={expanded}
	on:pointerdown={onPointerDown}
	on:pointermove={onPointerMove}
	on:pointerup={onPointerUp}
	on:pointercancel={onPointerUp}
	on:click={() => {
		if (dragged) dragged = false;
		else expanded = !expanded;
	}}
>
	<span class="w-10 h-1 rounded-full bg-gray-300 dark:bg-gray-600"></span>
</button>
