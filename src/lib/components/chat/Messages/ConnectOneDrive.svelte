<script lang="ts">
	import type { Readable } from 'svelte/store';
	import type { i18n as I18n } from 'i18next';
	import { getContext, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import {
		connectLiveSource,
		prefetchLiveConnections,
		consentLabels,
		relinkPrompt
	} from '$lib/utils/live-connections';
	const i18n = getContext<Readable<I18n>>('i18n');
	export let provider: string;
	onMount(() => {
		void prefetchLiveConnections(
			localStorage.token,
			provider,
			provider === 'outlook_mail' ? 'mail' : 'live_documents'
		).catch(() => {});
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
				await connectLiveSource(
					localStorage.token,
					provider,
					provider === 'outlook_mail' ? 'mail' : 'live_documents'
				);
				connected = true;
			} catch (error) {
				toast.error($i18n.t(relinkPrompt(error) ?? 'Could not connect account'));
			} finally {
				busy = false;
			}
		}}
	>
		{$i18n.t(
			connected
				? provider === 'outlook_mail'
					? 'Account connected. Enable mail search to continue.'
					: 'Account connected. Enable file search to continue.'
				: consentLabels[provider]
		)}
	</button>
</div>
