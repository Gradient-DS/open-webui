<script lang="ts">
	import { onMount, tick } from 'svelte';

	export let value = '';
	export let placeholder = '';
	export let rows = 1;
	export let minSize = null;
	export let maxSize = null;
	export let required = false;
	export let readonly = false;
	export let className =
		'w-full rounded-lg px-3.5 py-2 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden  h-full';
	export let ariaLabel = null;

	export let onInput = () => {};
	export let onBlur = () => {};

	let textareaElement;
	let scrollableParent = null;

	const findScrollableParent = (el) => {
		let parent = el?.parentElement;
		while (parent) {
			const { overflow, overflowY } = getComputedStyle(parent);
			if (/(auto|scroll)/.test(overflow + overflowY)) {
				return parent;
			}
			parent = parent.parentElement;
		}
		return null;
	};

	// Adjust height on mount and after setting the element.
	onMount(async () => {
		await tick();
		resize();

		requestAnimationFrame(() => {
			// setInterveal to cehck until textareaElement is set
			const interval = setInterval(() => {
				if (textareaElement) {
					clearInterval(interval);
					scrollableParent = findScrollableParent(textareaElement);
					resize();
				}
			}, 100);
		});
	});

	const resize = () => {
		if (textareaElement) {
			const savedScrollTop = scrollableParent?.scrollTop;

			textareaElement.style.height = '';

			let height = textareaElement.scrollHeight;
			if (maxSize && height > maxSize) {
				height = maxSize;
			}
			if (minSize && height < minSize) {
				height = minSize;
			}

			textareaElement.style.height = `${height}px`;

			if (scrollableParent != null) {
				scrollableParent.scrollTop = savedScrollTop;
			}
		}
	};
</script>

<textarea
	bind:this={textareaElement}
	bind:value
	{placeholder}
	aria-label={ariaLabel || placeholder}
	class={className}
	style="field-sizing: content;"
	{rows}
	{required}
	{readonly}
	on:input={(e) => {
		resize();

		onInput(e);
	}}
	on:focus={() => {
		if (!scrollableParent && textareaElement) {
			scrollableParent = findScrollableParent(textareaElement);
		}
		resize();
	}}
	on:blur={onBlur}
/>
