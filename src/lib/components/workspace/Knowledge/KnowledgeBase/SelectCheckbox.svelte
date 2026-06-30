<script lang="ts">
	import { getContext } from 'svelte';
	const i18n = getContext('i18n');

	export let selected = false;
	export let visible = false; // selection-mode forces the box visible
	export let selectable = true; // false → render an equal-size spacer so columns stay aligned
	export let onToggle: () => void = () => {};
</script>

{#if selectable}
	<div
		class="flex items-center transition-opacity {selected || visible
			? 'opacity-100'
			: 'opacity-0 group-hover:opacity-100'}"
	>
		<button
			type="button"
			class="p-1 rounded-full hover:bg-gray-100 dark:hover:bg-gray-850 transition"
			on:click|stopPropagation={onToggle}
			aria-label={$i18n.t('Select')}
		>
			<div
				class="size-3.5 shrink-0 rounded border flex items-center justify-center transition-colors {selected
					? 'bg-blue-500 dark:bg-blue-600 border-blue-500 dark:border-blue-600 text-white'
					: 'border-gray-300 dark:border-gray-600'}"
			>
				{#if selected}
					<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" class="size-2.5">
						<path
							fill-rule="evenodd"
							d="M16.704 4.153a.75.75 0 0 1 .143 1.052l-8 10.5a.75.75 0 0 1-1.127.075l-4.5-4.5a.75.75 0 0 1 1.06-1.06l3.894 3.893 7.48-9.817a.75.75 0 0 1 1.05-.143Z"
							clip-rule="evenodd"
						/>
					</svg>
				{/if}
			</div>
		</button>
	</div>
{:else}
	<!-- Spacer matching the checkbox footprint so spinner/icon columns line up -->
	<div class="flex items-center pointer-events-none" aria-hidden="true">
		<div class="p-1"><div class="size-3.5 shrink-0"></div></div>
	</div>
{/if}
