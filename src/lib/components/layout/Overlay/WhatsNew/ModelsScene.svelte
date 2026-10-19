<script lang="ts">
	// [Gradient] The model picker opens and the new models slide in, badged.
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	import type { AnnouncedModel } from '$lib/utils/whatsNew';
	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';

	const i18n = getContext<Writable<i18nType>>('i18n');

	let { models }: { models: AnnouncedModel[] } = $props();
</script>

<div class="flex flex-col items-center w-full max-w-sm">
	<div
		class="picker self-start inline-flex items-center gap-1 rounded-xl px-2.5 py-1 text-sm font-medium text-gray-800 dark:text-gray-100 bg-white/70 dark:bg-gray-850"
	>
		{models[0].name}
		<ChevronDown className="size-3.5" strokeWidth="2.5" />
	</div>
	<div
		class="menu mt-1.5 w-full rounded-2xl border border-gray-100 dark:border-gray-800 bg-white dark:bg-gray-850 shadow-lg p-1.5"
	>
		{#each models as model, i (model.name)}
			<div
				class="row flex items-center gap-2.5 rounded-xl px-2 py-1.5"
				style="--d: {0.35 + i * 0.2}s"
			>
				<span
					class="avatar size-7 shrink-0 rounded-full inline-flex items-center justify-center text-xs font-semibold text-white"
					style="--h: {[205, 265, 25][i % 3]}">{model.name[0]}</span
				>
				<div class="flex-1 min-w-0">
					<div class="text-sm font-medium text-gray-800 dark:text-gray-100 truncate">
						{model.name}
					</div>
					{#if model.description}
						<div class="text-[0.6875rem] text-gray-500 dark:text-gray-400 truncate">
							{$i18n.t(model.description)}
						</div>
					{/if}
				</div>
				<span
					class="badge shrink-0 rounded-full px-1.5 py-0.5 text-[0.625rem] font-semibold text-sky-700 bg-sky-100 dark:text-sky-200 dark:bg-sky-400/15"
					>{$i18n.t('New')}</span
				>
			</div>
		{/each}
	</div>
</div>

<style>
	.picker {
		animation: rise 0.4s ease-out both;
	}
	.menu {
		transform-origin: top left;
		animation: open 0.35s cubic-bezier(0.2, 0.8, 0.2, 1) 0.2s both;
	}
	.row {
		animation:
			slide 0.45s cubic-bezier(0.2, 0.8, 0.2, 1) both,
			focus 6s ease-in-out infinite;
		animation-delay: var(--d), calc(1.2s + var(--d) * 4);
	}
	.avatar {
		background: linear-gradient(135deg, hsl(var(--h) 80% 60%), hsl(calc(var(--h) + 40) 70% 45%));
	}
	.badge {
		animation: badge 0.4s cubic-bezier(0.3, 1.6, 0.5, 1) both;
		animation-delay: calc(var(--d) + 0.35s);
	}
	@keyframes rise {
		from {
			opacity: 0;
			transform: translateY(6px);
		}
	}
	@keyframes open {
		from {
			opacity: 0;
			transform: scale(0.92);
		}
	}
	@keyframes slide {
		from {
			opacity: 0;
			transform: translateX(-14px);
		}
	}
	@keyframes focus {
		0%,
		22%,
		100% {
			background: transparent;
		}
		6%,
		16% {
			background: rgb(148 163 184 / 0.14);
		}
	}
	@keyframes badge {
		from {
			opacity: 0;
			transform: scale(0.4);
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.picker,
		.menu,
		.row,
		.badge {
			animation: none;
		}
	}
</style>
