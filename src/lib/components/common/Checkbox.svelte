<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	const dispatch = createEventDispatcher();

	export let state: 'checked' | 'unchecked' = 'unchecked';
	export let indeterminate = false;
	export let disabled = false;

	export let disabledClassName = 'opacity-50 cursor-not-allowed';

	// Determine visual state: use prop directly for consistent rendering
	$: isChecked = state === 'checked';
	$: showCheck = isChecked && !indeterminate;
	$: showIndeterminate = indeterminate && !isChecked;
	$: hasBackground = isChecked || indeterminate;
</script>

<button
	class="outline -outline-offset-1 outline-[1.5px] outline-gray-200 dark:outline-gray-600 {hasBackground
		? 'bg-black outline-black dark:bg-white dark:outline-white'
		: 'hover:outline-gray-500 hover:bg-gray-50 dark:hover:bg-gray-800'} text-white dark:text-black transition-all rounded-sm inline-block w-3.5 h-3.5 relative {disabled
		? disabledClassName
		: ''}"
	on:click={() => {
		if (disabled) return;

		// Toggle state and dispatch
		if (state === 'unchecked' || indeterminate) {
			dispatch('change', 'checked');
		} else {
			dispatch('change', 'unchecked');
		}
	}}
	type="button"
	{disabled}
>
	<div class="top-0 left-0 absolute w-full flex justify-center">
		{#if showCheck}
			<svg
				class="w-3.5 h-3.5"
				aria-hidden="true"
				xmlns="http://www.w3.org/2000/svg"
				fill="none"
				viewBox="0 0 24 24"
			>
				<path
					stroke="currentColor"
					stroke-linecap="round"
					stroke-linejoin="round"
					stroke-width="3"
					d="m5 12 4.7 4.5 9.3-9"
				/>
			</svg>
		{:else if indeterminate}
			<svg
				class="w-3 h-3.5"
				aria-hidden="true"
				xmlns="http://www.w3.org/2000/svg"
				fill="none"
				viewBox="0 0 24 24"
			>
				<path
					stroke="currentColor"
					stroke-linecap="round"
					stroke-linejoin="round"
					stroke-width="3"
					d="M5 12h14"
				/>
			</svg>
		{/if}
	</div>
</button>
