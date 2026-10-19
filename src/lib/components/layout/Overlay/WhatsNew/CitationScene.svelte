<script lang="ts">
	// [Gradient] A source number is clicked; the side panel opens the PDF with the passage marked.
</script>

<div class="scene flex items-stretch gap-2 h-44 w-full max-w-sm">
	<div
		class="flex-1 rounded-2xl bg-gray-50 dark:bg-gray-850 p-3 flex flex-col gap-1.5 justify-center"
	>
		<div class="line w-11/12"></div>
		<div class="line w-10/12"></div>
		<div class="flex items-center gap-1">
			<div class="line w-7/12"></div>
			<span
				class="cite shrink-0 inline-flex items-center justify-center h-4 min-w-4 px-1 rounded text-[0.625rem] font-semibold"
				>1</span
			>
		</div>
		<div class="line w-9/12"></div>
		<div class="line w-5/12"></div>
	</div>
	<div
		class="panel w-36 rounded-2xl border border-gray-100 dark:border-gray-800 bg-white dark:bg-gray-900 shadow-lg p-2 flex flex-col"
	>
		<div class="flex items-center gap-1 mb-1.5">
			<span class="text-[0.5625rem] font-bold text-white bg-red-500 rounded px-1">PDF</span>
			<div class="line w-12"></div>
			<span class="ml-auto text-[0.5625rem] text-gray-400">p. 4</span>
		</div>
		<div class="page flex-1 rounded-md bg-white border border-gray-100 p-2 flex flex-col gap-1">
			<div class="pline w-full"></div>
			<div class="pline w-10/12"></div>
			<div class="relative flex flex-col gap-1">
				<span class="mark" aria-hidden="true"></span>
				<div class="pline w-full"></div>
				<div class="pline w-8/12"></div>
			</div>
			<div class="pline w-11/12"></div>
			<div class="pline w-9/12"></div>
			<div class="pline w-6/12"></div>
		</div>
	</div>
</div>

<style>
	.line {
		height: 0.4rem;
		border-radius: 9999px;
		background: rgb(148 163 184 / 0.35);
	}
	.pline {
		height: 0.3rem;
		border-radius: 9999px;
		background: rgb(148 163 184 / 0.45);
	}
	.cite {
		color: rgb(2 132 199);
		background: rgb(224 242 254);
		animation: cite 5s ease-in-out infinite;
	}
	:global(.dark) .cite {
		color: rgb(125 211 252);
		background: rgb(56 189 248 / 0.15);
	}
	.panel {
		animation: panel 5s cubic-bezier(0.2, 0.8, 0.2, 1) infinite;
	}
	.mark {
		position: absolute;
		inset: -3px -4px;
		border-radius: 3px;
		background: rgb(250 204 21 / 0.55);
		box-shadow: 0 0 0 1px rgb(234 179 8 / 0.6);
		transform-origin: left;
		animation: mark 5s ease-out infinite;
	}
	@keyframes cite {
		0%,
		12%,
		100% {
			transform: scale(1);
			box-shadow: none;
		}
		16% {
			transform: scale(1.35);
			box-shadow: 0 0 0 4px rgb(14 165 233 / 0.25);
		}
		20% {
			transform: scale(1);
		}
	}
	@keyframes panel {
		0%,
		18% {
			opacity: 0;
			transform: translateX(24px);
		}
		30%,
		90% {
			opacity: 1;
			transform: none;
		}
		100% {
			opacity: 0;
			transform: translateX(24px);
		}
	}
	@keyframes mark {
		0%,
		36% {
			opacity: 1;
			transform: scaleX(0);
		}
		50%,
		90% {
			opacity: 1;
			transform: scaleX(1);
		}
		100% {
			transform: scaleX(1);
			opacity: 0;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.cite,
		.panel,
		.mark {
			animation: none;
		}
	}
</style>
