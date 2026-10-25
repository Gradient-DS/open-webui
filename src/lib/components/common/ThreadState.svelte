<script lang="ts">
	import { getContext } from 'svelte';
	import type { Readable } from 'svelte/store';
	import type { i18n as I18n } from 'i18next';
	import type { ThreadState } from '$lib/apis/threads';

	let { state, retry }: { state: ThreadState; retry: () => unknown } = $props();
	const i18n = getContext<Readable<I18n>>('i18n');
	const text = $derived({
		reconnect: $i18n.t('Reconnect to view this chat'),
		unavailable: $i18n.t('This chat is unavailable'),
		sign_in: $i18n.t('Sign in again to view this chat'),
		invalid_request: $i18n.t('Reload this chat to continue')
	});
</script>

<p role="status">{text[state]}</p>
<button onclick={retry}>{$i18n.t('Retry')}</button>
