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

	const getErrorMessage = (value: unknown): string => {
		if (typeof value === 'string') {
			return value;
		}

		if (typeof value === 'object' && value !== null) {
			const error = 'error' in value ? value.error : null;

			if (
				typeof error === 'object' &&
				error !== null &&
				'message' in error &&
				typeof error.message === 'string'
			) {
				return error.message;
			}

			if ('detail' in value && typeof value.detail === 'string') {
				return value.detail;
			}

			if ('message' in value && typeof value.message === 'string') {
				return value.message;
			}

			return JSON.stringify(value) ?? String(value);
		}

		return JSON.stringify(value) ?? String(value);
	};

	// [Gradient] Keep the report payload aligned with the technical-details disclosure.
	$: errorText = getErrorMessage(content);

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

<div
	class="my-1.5 flex w-full items-start gap-2 rounded-2xl bg-black/[0.03] px-3 py-2 text-gray-500 dark:bg-white/[0.04] dark:text-gray-400"
>
	<Info className="mt-0.5 size-4 shrink-0 text-gray-400 dark:text-gray-500" strokeWidth="1.8" />
	<div class="min-w-0 flex flex-col gap-1.5 break-words text-[0.8125rem] leading-5">
		<div>{$i18n.t('There was a problem generating a response.')}</div>

		{#if errorText}
			<details class="text-xs text-gray-500 dark:text-gray-400">
				<summary
					class="cursor-pointer select-none hover:text-gray-700 dark:hover:text-gray-300 hover:underline transition"
				>
					{$i18n.t('Technical details')}
				</summary>
				<div
					class="mt-1 whitespace-pre-wrap break-words font-mono text-gray-500 dark:text-gray-400"
				>
					{errorText}
				</div>
			</details>
		{/if}

		{#if $config?.features?.enable_feedback_report}
			<button
				type="button"
				class="self-start text-xs text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300 hover:underline transition"
				on:click={reportProblem}
			>
				{$i18n.t('Report this problem')}
			</button>
		{/if}
	</div>
</div>
