<script lang="ts">
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	const i18n = getContext<Writable<i18nType>>('i18n');

	import { showFeedbackModal } from '$lib/stores';
	import ChatBubbleOval from '$lib/components/icons/ChatBubbleOval.svelte';
</script>

<button
	type="button"
	aria-label={$i18n.t('Send feedback')}
	title={$i18n.t('Send feedback')}
	on:click={() => showFeedbackModal.set(true)}
	class="flex items-center justify-center size-10 rounded-full bg-black hover:bg-gray-950 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 shadow-lg transition"
>
	<ChatBubbleOval className="size-5" strokeWidth="1.5" />
</button>
