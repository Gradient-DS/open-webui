<script lang="ts">
	// [Gradient] The + menu rows flipping from Off to Auto.
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	import GlobeAlt from '$lib/components/icons/GlobeAlt.svelte';
	import Document from '$lib/components/icons/Document.svelte';
	import ToolStateLabel from '$lib/components/chat/MessageInput/InputMenu/ToolStateLabel.svelte';

	const i18n = getContext<Writable<i18nType>>('i18n');

	let { webSearch, documentWriter }: { webSearch: boolean; documentWriter: boolean } = $props();

	const rows = $derived(
		[
			webSearch && { label: 'Search the web', icon: GlobeAlt },
			documentWriter && { label: 'Document writer', icon: Document }
		].filter((row) => !!row)
	);
</script>

<div class="scene">
	<div
		class="w-64 rounded-2xl border border-gray-100 dark:border-gray-800 bg-white dark:bg-gray-850 shadow-lg p-1.5 menu"
	>
		{#each rows as row, i}
			<div
				class="flex items-center gap-2 px-2.5 py-2 rounded-xl text-sm text-gray-700 dark:text-gray-200 row"
				style="--d: {i * 0.9}s"
			>
				<row.icon className="size-4" />
				<span class="flex-1">{$i18n.t(row.label)}</span>
				<span class="relative inline-flex">
					<span class="chip-off"><ToolStateLabel state="off" /></span>
					<span class="chip-auto absolute inset-0"><ToolStateLabel state="auto" /></span>
					<span class="ripple" aria-hidden="true"></span>
				</span>
			</div>
		{/each}
	</div>
	<div class="mt-4 flex items-center gap-1.5 text-xs text-gray-500 dark:text-gray-400 hint">
		<span class="size-1.5 rounded-full bg-sky-500 pulse"></span>
		{$i18n.t('soev.ai decides per question')}
	</div>
</div>

<style>
	.scene {
		display: flex;
		flex-direction: column;
		align-items: center;
	}
	.menu {
		animation: rise 0.5s ease-out both;
	}
	.row {
		animation: row-focus 4.5s ease-in-out infinite;
		animation-delay: var(--d);
	}
	.chip-off {
		animation: chip-out 4.5s ease-in-out infinite;
		animation-delay: var(--d);
	}
	.chip-auto {
		opacity: 0;
		animation: chip-in 4.5s ease-in-out infinite;
		animation-delay: var(--d);
	}
	.ripple {
		position: absolute;
		inset: -4px;
		border-radius: 0.5rem;
		border: 2px solid rgb(14 165 233 / 0.6);
		opacity: 0;
		animation: ripple 4.5s ease-out infinite;
		animation-delay: var(--d);
	}
	.hint {
		animation: rise 0.5s ease-out 1.6s both;
	}
	.pulse {
		animation: pulse 1.6s ease-in-out infinite;
	}
	@keyframes rise {
		from {
			opacity: 0;
			transform: translateY(8px);
		}
	}
	@keyframes row-focus {
		0%,
		10%,
		40%,
		100% {
			background: transparent;
		}
		18%,
		32% {
			background: rgb(148 163 184 / 0.12);
		}
	}
	@keyframes chip-out {
		0%,
		22% {
			opacity: 1;
		}
		28%,
		88% {
			opacity: 0;
		}
		94%,
		100% {
			opacity: 1;
		}
	}
	@keyframes chip-in {
		0%,
		22% {
			opacity: 0;
			transform: scale(0.8);
		}
		28%,
		88% {
			opacity: 1;
			transform: scale(1);
		}
		94%,
		100% {
			opacity: 0;
		}
	}
	@keyframes ripple {
		0%,
		20% {
			opacity: 0;
			transform: scale(0.9);
		}
		23% {
			opacity: 1;
		}
		34%,
		100% {
			opacity: 0;
			transform: scale(1.25);
		}
	}
	@keyframes pulse {
		50% {
			opacity: 0.3;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.menu,
		.row,
		.chip-off,
		.ripple,
		.hint,
		.pulse {
			animation: none;
		}
		.chip-off {
			opacity: 0;
		}
		.chip-auto {
			animation: none;
			opacity: 1;
		}
	}
</style>
