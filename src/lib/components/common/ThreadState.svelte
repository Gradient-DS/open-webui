<script lang="ts">
	import { getContext } from 'svelte';
	import type { Readable } from 'svelte/store';
	import type { i18n as I18n } from 'i18next';
	import type { ThreadState } from '$lib/apis/threads';
	import { recoverSession, sessionRedirecting } from '$lib/stores/session';

	let { state, retry }: { state: ThreadState; retry: () => unknown } = $props();
	const i18n = getContext<Readable<I18n>>('i18n');
	$effect(() => {
		void recoverSession(state);
	});
	const text = $derived({
		reconnect: $i18n.t('Reconnect to view this chat'),
		unavailable: $i18n.t('This chat is unavailable'),
		sign_in: $i18n.t('Sign in again to view this chat'),
		invalid_request: $i18n.t('Reload this chat to continue'),
		share_refused: $i18n.t("This chat uses sources you don't have access to"),
		legacy_share: $i18n.t('This chat is no longer available to others')
	});
</script>

{#if state !== 'reconnect' && state !== 'sign_in' && !$sessionRedirecting}
	<p role="status">{text[state]}</p>
	<button onclick={retry}>{$i18n.t('Retry')}</button>
{/if}
