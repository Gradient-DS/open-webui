<script lang="ts">
	// [Gradient] A question about your mail; matching mails fly in and the answer cites one.
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	import Outlook from '$lib/components/icons/Outlook.svelte';

	const i18n = getContext<Writable<i18nType>>('i18n');
</script>

<div class="flex flex-col gap-2 w-full max-w-sm">
	<div
		class="question self-end max-w-[85%] rounded-2xl rounded-br-md bg-gray-100 dark:bg-gray-800 px-3 py-1.5 text-xs text-gray-700 dark:text-gray-200"
	>
		{$i18n.t('What did the contractor send about the schedule?')}
	</div>
	<div class="flex flex-col gap-1.5">
		{#each [0, 1, 2] as i}
			<div
				class="mail flex items-center gap-2 rounded-xl border border-gray-100 dark:border-gray-800 bg-white dark:bg-gray-850 px-2.5 py-1.5"
				class:hit={i === 1}
				style="--d: {0.9 + i * 0.25}s"
			>
				<Outlook className="size-4 shrink-0" />
				<div class="flex-1 flex flex-col gap-1">
					<div class="line" style="width: {[55, 70, 45][i]}%"></div>
					<div class="line faint" style="width: {[80, 60, 75][i]}%"></div>
				</div>
			</div>
		{/each}
	</div>
	<div class="answer flex items-center gap-1.5">
		<div class="line flex-1"></div>
		<span
			class="inline-flex items-center gap-1 rounded-full bg-gray-100 dark:bg-gray-800 px-1.5 py-0.5"
		>
			<Outlook className="size-3" />
			<span class="line w-8"></span>
		</span>
	</div>
</div>

<style>
	.line {
		height: 0.35rem;
		border-radius: 9999px;
		background: rgb(148 163 184 / 0.45);
	}
	.faint {
		background: rgb(148 163 184 / 0.25);
	}
	.question {
		animation: pop 6s ease-out infinite;
	}
	.mail {
		animation: mail 6s cubic-bezier(0.2, 0.8, 0.2, 1) infinite;
		animation-delay: var(--d);
		opacity: 0;
	}
	.hit {
		animation-name: mail-hit;
	}
	.answer {
		opacity: 0;
		animation: answer 6s ease-out infinite;
	}
	@keyframes pop {
		0% {
			opacity: 0;
			transform: translateY(6px) scale(0.96);
		}
		6%,
		88% {
			opacity: 1;
			transform: none;
		}
		100% {
			opacity: 0;
		}
	}
	@keyframes mail {
		0% {
			opacity: 0;
			transform: translateX(-20px);
		}
		8%,
		70% {
			opacity: 1;
			transform: none;
		}
		78%,
		100% {
			opacity: 0;
		}
	}
	@keyframes mail-hit {
		0% {
			opacity: 0;
			transform: translateX(-20px);
		}
		8% {
			opacity: 1;
			transform: none;
		}
		22%,
		70% {
			opacity: 1;
			border-color: rgb(14 165 233 / 0.7);
			box-shadow: 0 0 0 3px rgb(14 165 233 / 0.15);
		}
		78%,
		100% {
			opacity: 0;
		}
	}
	@keyframes answer {
		0%,
		45% {
			opacity: 0;
			transform: translateY(6px);
		}
		52%,
		88% {
			opacity: 1;
			transform: none;
		}
		100% {
			opacity: 0;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.question,
		.mail,
		.answer {
			animation: none;
			opacity: 1;
		}
	}
</style>
