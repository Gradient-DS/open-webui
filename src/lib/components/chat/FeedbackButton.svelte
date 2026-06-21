<script lang="ts">
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	const i18n = getContext<Writable<i18nType>>('i18n');

	import { config, showFeedbackModal, feedbackModalContext } from '$lib/stores';
	import { showRagFilter } from '$lib/stores/rag-filter';
	import ChatBubbleOval from '$lib/components/icons/ChatBubbleOval.svelte';

	const openFeedback = () => {
		// Open the shared feedback modal defaulting to "Question" rather than a
		// bug report. The hint only seeds the initial category in the modal and
		// is never part of the submitted payload.
		feedbackModalContext.set({ default_category: 'question' });
		showFeedbackModal.set(true);
	};
</script>

<!-- Hidden while the RAG filter panel is open so it never sits behind it. -->
{#if ($config?.features?.enable_feedback_report ?? false) && !$showRagFilter}
	<button
		type="button"
		aria-label={$i18n.t('Send feedback')}
		title={$i18n.t('Send feedback')}
		on:click={openFeedback}
		class="absolute bottom-3 right-3 z-20 flex items-center justify-center size-10 rounded-full bg-black hover:bg-gray-950 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 shadow-lg transition"
	>
		<ChatBubbleOval className="size-5" strokeWidth="1.5" />
	</button>
{/if}
