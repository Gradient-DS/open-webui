<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import { config } from '$lib/stores';
	import {
		createNewFeedback,
		updateFeedbackById,
		getConversationFeedback
	} from '$lib/apis/evaluations';
	import { toast } from 'svelte-sonner';
	import { slide } from 'svelte/transition';

	const i18n = getContext('i18n');

	export let chatId: string;
	export let assistantMessageCount: number = 0;

	let expanded = false;
	let selectedRating: number | null = null;
	let comment = '';
	let feedbackId: string | null = null;
	let submitted = false;

	$: scaleMax = $config?.features?.conversation_feedback_scale_max ?? 5;
	$: header =
		$config?.features?.conversation_feedback_header ||
		$i18n.t('How was this conversation?');
	$: placeholder =
		$config?.features?.conversation_feedback_placeholder ||
		$i18n.t('Any thoughts on the overall conversation?');
	$: enabled = ($config?.features?.enable_conversation_feedback ?? false) && assistantMessageCount >= 2;

	// Load existing conversation feedback when chatId changes
	$: if (chatId && enabled) {
		loadExistingFeedback();
	}

	const loadExistingFeedback = async () => {
		try {
			const feedback = await getConversationFeedback(localStorage.token, chatId);
			if (feedback) {
				feedbackId = feedback.id;
				selectedRating = feedback.data?.rating ?? null;
				comment = feedback.data?.comment ?? '';
				submitted = true;
			} else {
				feedbackId = null;
				selectedRating = null;
				comment = '';
				submitted = false;
			}
		} catch {
			// No existing feedback, that's fine
		}
	};

	const submitFeedback = async () => {
		if (!selectedRating) return;

		const feedbackItem = {
			type: 'rating',
			data: {
				rating: selectedRating,
				comment: comment || undefined
			},
			meta: {
				scope: 'conversation',
				chat_id: chatId,
				scale_max: scaleMax
			},
			snapshot: {}
		};

		try {
			if (feedbackId) {
				await updateFeedbackById(localStorage.token, feedbackId, feedbackItem);
			} else {
				const feedback = await createNewFeedback(localStorage.token, feedbackItem);
				if (feedback) feedbackId = feedback.id;
			}
			submitted = true;
			expanded = false;
			toast.success($i18n.t('Thanks for your feedback!'));
		} catch (error) {
			toast.error(`${error}`);
		}
	};
</script>

{#if enabled}
	<div class="w-full max-w-4xl mx-auto px-4">
		{#if !expanded}
			<!-- Collapsed: thin clickable divider -->
			<button
				class="w-full flex items-center gap-3 py-1.5 text-xs text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-400 transition group"
				on:click={() => {
					expanded = true;
				}}
			>
				<div
					class="flex-1 h-px bg-gray-200 dark:bg-gray-700 group-hover:bg-gray-300 dark:group-hover:bg-gray-600 transition"
				/>
				<span class="whitespace-nowrap"
					>{submitted
						? $i18n.t('Feedback submitted')
						: header}</span
				>
				<div
					class="flex-1 h-px bg-gray-200 dark:bg-gray-700 group-hover:bg-gray-300 dark:group-hover:bg-gray-600 transition"
				/>
			</button>
		{/if}

		{#if expanded}
			<!-- Expanded: rating + free text -->
			<div
				class="bg-white/5 dark:bg-gray-500/5 backdrop-blur-sm border border-gray-100/30 dark:border-gray-850/30 rounded-3xl px-4 py-3 mb-2 shadow-lg"
				transition:slide={{ duration: 200 }}
			>
				<div class="flex justify-between items-center mb-2">
					<div class="text-sm font-medium">{header}</div>
					<button
						class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
						on:click={() => {
							expanded = false;
						}}
					>
						<svg
							class="w-4 h-4"
							fill="none"
							stroke="currentColor"
							stroke-width="2"
							viewBox="0 0 24 24"
						>
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								d="M6 18L18 6M6 6l12 12"
							/>
						</svg>
					</button>
				</div>

				<div class="flex gap-1.5 mb-2">
					{#each Array.from({ length: scaleMax }).map((_, i) => i + 1) as rating}
						<button
							class="size-8 text-sm border border-gray-100/30 dark:border-gray-850/30 hover:bg-gray-50 dark:hover:bg-gray-850 {selectedRating ===
							rating
								? 'bg-gray-100 dark:bg-gray-800'
								: ''} transition rounded-full"
							on:click={() => {
								selectedRating = rating;
							}}
						>
							{rating}
						</button>
					{/each}
				</div>

				<textarea
					bind:value={comment}
					class="w-full text-sm px-1 py-2 bg-transparent outline-hidden resize-none rounded-xl"
					placeholder={placeholder}
					rows="2"
				/>

				<div class="flex justify-end mt-1">
					<button
						class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full disabled:opacity-50 disabled:cursor-not-allowed"
						disabled={!selectedRating}
						on:click={submitFeedback}
					>
						{$i18n.t('Submit')}
					</button>
				</div>
			</div>
		{/if}
	</div>
{/if}
