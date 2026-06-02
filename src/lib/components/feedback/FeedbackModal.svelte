<script lang="ts">
	import { getContext, tick } from 'svelte';
	const i18n = getContext('i18n');

	import { toast } from 'svelte-sonner';
	import { page } from '$app/stores';

	import { WEBUI_VERSION } from '$lib/constants';
	import { showFeedbackModal, feedbackModalContext } from '$lib/stores';
	import { submitFeedbackReport } from '$lib/apis/feedback';

	import Modal from '$lib/components/common/Modal.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';

	let category = 'bug';
	let description = '';
	let loading = false;
	let errorContext: Record<string, unknown> | null = null;

	// `errorContext` is populated when the modal is opened from a chat error
	// message (Phase 2). When present, the category is locked to "error".
	$: isErrorReport = !!errorContext?.error_message;

	$: categoryOptions = [
		{ value: 'bug', label: $i18n.t('Bug') },
		{ value: 'idea', label: $i18n.t('Idea') },
		{ value: 'question', label: $i18n.t('Question') },
		{ value: 'other', label: $i18n.t('Other') }
	];

	$: if ($showFeedbackModal) {
		onOpen();
	} else {
		onClose();
	}

	const onOpen = async () => {
		errorContext = $feedbackModalContext ?? null;
		category = errorContext?.error_message ? 'error' : 'bug';
		description = '';
		loading = false;

		await tick();
		document.getElementById('feedback-description')?.focus();
	};

	const onClose = () => {
		// Drop any error pre-fill so the next open starts clean.
		feedbackModalContext.set(null);
		errorContext = null;
	};

	const submitHandler = async () => {
		if (!description.trim() || loading) {
			return;
		}
		loading = true;

		// Only allowlisted context keys — the backend rejects anything else,
		// the structural guarantee that no chat content leaks through.
		const context = {
			route: $page.url.pathname,
			app_version: WEBUI_VERSION,
			user_agent: navigator.userAgent,
			...(errorContext ?? {})
		};

		try {
			await submitFeedbackReport(localStorage.token, {
				category,
				description: description.trim(),
				context
			});
			toast.success($i18n.t('Thanks for your feedback!'));
			showFeedbackModal.set(false);
		} catch {
			toast.error($i18n.t('Failed to send feedback'));
		} finally {
			loading = false;
		}
	};
</script>

<Modal size="sm" bind:show={$showFeedbackModal}>
	<div>
		<div class="flex justify-between dark:text-gray-300 px-5 pt-4 pb-1">
			<div class="text-lg font-medium self-center">
				{isErrorReport ? $i18n.t('Report a problem') : $i18n.t('Share feedback')}
			</div>
			<button class="self-center" type="button" on:click={() => showFeedbackModal.set(false)}>
				<XMark className={'size-5'} />
			</button>
		</div>

		<div class="flex flex-col w-full px-5 pb-4 dark:text-gray-200">
			<form class="flex flex-col w-full" on:submit|preventDefault={submitHandler}>
				{#if !isErrorReport}
					<div class="mb-3">
						<div class="text-xs text-gray-500 mb-1.5">{$i18n.t('Category')}</div>
						<div
							class="flex items-center px-2.5 py-2 border border-gray-100/50 dark:border-gray-850/50 rounded-xl"
						>
							<select
								class="w-full text-sm bg-transparent outline-hidden"
								bind:value={category}
							>
								{#each categoryOptions as option (option.value)}
									<option value={option.value} class="dark:bg-gray-900">{option.label}</option>
								{/each}
							</select>
						</div>
					</div>
				{/if}

				<div>
					<div class="text-xs text-gray-500 mb-1.5">{$i18n.t('Description')}</div>
					<textarea
						id="feedback-description"
						bind:value={description}
						class="w-full text-sm bg-transparent border border-gray-100/50 dark:border-gray-850/50 rounded-xl px-2.5 py-2 outline-hidden resize-none placeholder:text-gray-300 dark:placeholder:text-gray-700"
						rows="4"
						maxlength="5000"
						placeholder={$i18n.t('Describe your feedback')}
						required
					></textarea>
				</div>

				<div class="mt-2 text-xs text-gray-400 dark:text-gray-600">
					{$i18n.t(
						"We attach the page you're on and the app version. We never send your chat content."
					)}
				</div>

				<div class="flex justify-end pt-3 text-sm font-medium gap-1.5">
					<button
						class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-950 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full flex flex-row space-x-1 items-center {loading ||
						!description.trim()
							? 'cursor-not-allowed opacity-50'
							: ''}"
						type="submit"
						disabled={loading || !description.trim()}
					>
						{$i18n.t('Submit')}

						{#if loading}
							<div class="ml-2 self-center">
								<Spinner />
							</div>
						{/if}
					</button>
				</div>
			</form>
		</div>
	</div>
</Modal>
