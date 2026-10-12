<script lang="ts">
	// [Gradient] Demo: a visible Excel switch for the Office feature, in the composer bar.
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	const i18n = getContext<Writable<i18nType>>('i18n');

	export let officeEnabled = false;
	export let officeEditEnabled = false;

	const toggle = () => {
		officeEnabled = !officeEnabled;
		// Editing follows, so a follow-up turn can change the workbook; MessageInput clears it where not allowed.
		officeEditEnabled = officeEnabled;
	};
</script>

<button
	type="button"
	aria-pressed={officeEnabled}
	aria-label={$i18n.t('Excel')}
	on:click|preventDefault={toggle}
	class="px-2.5 py-[0.3125rem] flex gap-1.5 items-center text-sm font-medium rounded-full border transition-colors duration-300 focus:outline-hidden shrink-0 {officeEnabled
		? 'text-white bg-emerald-600 hover:bg-emerald-700 border-emerald-600 dark:bg-emerald-500 dark:hover:bg-emerald-600'
		: 'text-emerald-700 dark:text-emerald-300 bg-transparent border-emerald-600/40 hover:bg-emerald-50 dark:hover:bg-emerald-500/10'}"
>
	<svg
		xmlns="http://www.w3.org/2000/svg"
		viewBox="0 0 24 24"
		fill="none"
		stroke="currentColor"
		stroke-width="1.75"
		stroke-linecap="round"
		stroke-linejoin="round"
		class="size-4"
		aria-hidden="true"
	>
		<rect x="3.5" y="3.5" width="17" height="17" rx="2" />
		<path d="M3.5 9h17M3.5 14.5h17M9.5 3.5v17" />
	</svg>
	<span>{$i18n.t('Excel')}</span>
</button>
