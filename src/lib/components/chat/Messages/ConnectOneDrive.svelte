<script lang="ts">
	import type { Readable } from 'svelte/store';
	import type { i18n as I18n } from 'i18next';
	import { getContext, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import {
		connectLiveDocuments,
		prefetchLiveDocuments,
		consentLabels
	} from '$lib/utils/live-documents';
	const i18n = getContext<Readable<I18n>>('i18n');
	export let provider: string;
	onMount(() => {
		void prefetchLiveDocuments(localStorage.token, provider).catch(() => {});
	});
	let busy = false;
	let connected = false;
</script>

<div class="my-2 rounded-xl border border-gray-200 p-3 dark:border-gray-700">
	<button
		type="button"
		disabled={busy || connected}
		class="text-sm font-medium text-blue-600 dark:text-blue-400"
		on:click={async () => {
			busy = true;
			try {
				await connectLiveDocuments(localStorage.token, provider);
				connected = true;
			} catch {
				toast.error($i18n.t('Could not connect account'));
			} finally {
				busy = false;
			}
		}}
	>
		{$i18n.t(
			connected ? 'Account connected. Enable file search to continue.' : consentLabels[provider]
		)}
	</button>
</div>
