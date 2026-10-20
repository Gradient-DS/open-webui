<script lang="ts">
	// [Gradient] A magnifier sweeps a OneDrive folder; the found files light up.
	import OneDrive from '$lib/components/icons/OneDrive.svelte';
	import Search from '$lib/components/icons/Search.svelte';

	const HITS = [1, 4];
</script>

<div
	class="relative w-full max-w-xs rounded-2xl border border-gray-100 dark:border-gray-800 bg-white dark:bg-gray-850 shadow-lg p-3"
>
	<div class="flex items-center gap-2 mb-3">
		<OneDrive className="size-5" />
		<div class="line w-20"></div>
	</div>
	<div class="grid grid-cols-3 gap-2">
		{#each Array(6) as _, i}
			<div
				class="tile rounded-lg border border-gray-100 dark:border-gray-800 p-2 flex flex-col gap-1.5"
				class:hit={HITS.includes(i)}
				style="--d: {0.4 + i * 0.25}s"
			>
				<div class="doc h-6 w-5 rounded-sm"></div>
				<div class="line w-full"></div>
			</div>
		{/each}
	</div>
	<div class="lens absolute text-gray-600 dark:text-gray-300" aria-hidden="true">
		<Search className="size-7" strokeWidth="2" />
	</div>
</div>

<style>
	.line {
		height: 0.3rem;
		border-radius: 9999px;
		background: rgb(148 163 184 / 0.4);
	}
	.doc {
		background: rgb(148 163 184 / 0.3);
	}
	.tile {
		animation: rise 0.4s ease-out both;
		animation-delay: calc(var(--d) * 0.4);
	}
	.hit {
		animation:
			rise 0.4s ease-out both,
			hit 5s ease-in-out infinite;
		animation-delay: calc(var(--d) * 0.4), 0s;
	}
	.hit .doc {
		animation: hit-doc 5s ease-in-out infinite;
	}
	.lens {
		top: 2.5rem;
		left: 0.5rem;
		animation: sweep 5s ease-in-out infinite;
	}
	@keyframes rise {
		from {
			opacity: 0;
			transform: translateY(6px);
		}
	}
	@keyframes sweep {
		0% {
			transform: translate(0, 0);
		}
		20% {
			transform: translate(5.5rem, 0);
		}
		40% {
			transform: translate(11rem, 0);
		}
		60% {
			transform: translate(5.5rem, 3.5rem);
		}
		80% {
			transform: translate(0, 3.5rem);
		}
		100% {
			transform: translate(0, 0);
		}
	}
	@keyframes hit {
		0%,
		15%,
		95%,
		100% {
			border-color: rgb(148 163 184 / 0.15);
			box-shadow: none;
		}
		22%,
		88% {
			border-color: rgb(14 165 233 / 0.7);
			box-shadow: 0 0 0 3px rgb(14 165 233 / 0.15);
		}
	}
	@keyframes hit-doc {
		0%,
		15%,
		95%,
		100% {
			background: rgb(148 163 184 / 0.3);
		}
		22%,
		88% {
			background: rgb(14 165 233 / 0.6);
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.tile,
		.hit,
		.hit .doc,
		.lens {
			animation: none;
		}
	}
</style>
