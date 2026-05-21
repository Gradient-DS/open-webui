<script lang="ts">
	import { getContext } from 'svelte';
	import { page } from '$app/stores';

	import { config, chatId, showFeedbackModal, feedbackModalContext } from '$lib/stores';

	import Info from '$lib/components/icons/Info.svelte';

	const i18n = getContext('i18n');

	type ErrorContent =
		| string
		| {
				content?: string;
				detail?: string;
				message?: string;
				error?: { message?: string };
				trace_id?: string;
		  };

	export let content: ErrorContent = '';
	// `message.error` is either a structured object or the legacy `true` flag.
	export let error: boolean | { trace_id?: string; content?: string } | null = null;
	export let model: string | null = null;

	// Mirror the render branches below into one plain string for the report.
	$: errorText =
		typeof content === 'string'
			? content
			: (content?.error?.message ?? content?.detail ?? content?.message ?? JSON.stringify(content));

	const reportProblem = () => {
		const objContent = typeof content === 'object' && content !== null ? content : null;
		const objError = typeof error === 'object' && error !== null ? error : null;
		feedbackModalContext.set({
			error_message: errorText,
			error_detail: objContent?.detail ?? null,
			trace_id: objError?.trace_id ?? objContent?.trace_id ?? null,
			model: model ?? null,
			chat_id: $chatId || null,
			route: $page.url.pathname
		});
		showFeedbackModal.set(true);
	};
</script>

<div class="flex flex-col my-2 gap-1.5 border px-4 py-3 border-red-600/10 bg-red-600/10 rounded-lg">
	<div class="flex gap-2.5">
		<div class=" self-start mt-0.5">
			<Info className="size-5 text-red-700 dark:text-red-400" />
		</div>

		<div class=" self-center text-sm">
			{$i18n.t('There was a problem generating a response.')}
		</div>
	</div>

	{#if errorText}
		<details class="pl-[1.875rem] text-xs text-red-700/80 dark:text-red-400/80">
			<summary
				class="cursor-pointer select-none hover:text-red-700 dark:hover:text-red-400 hover:underline transition"
			>
				{$i18n.t('Technical details')}
			</summary>
			<div
				class="mt-1 whitespace-pre-wrap break-words font-mono text-red-700/80 dark:text-red-400/80"
			>
				{errorText}
			</div>
		</details>
	{/if}

	{#if $config?.features?.enable_feedback_report}
		<button
			type="button"
			class="self-start pl-[1.875rem] text-xs text-red-700/70 dark:text-red-400/70 hover:text-red-700 dark:hover:text-red-400 hover:underline transition"
			on:click={reportProblem}
		>
			{$i18n.t('Report this problem')}
		</button>
	{/if}
</div>
