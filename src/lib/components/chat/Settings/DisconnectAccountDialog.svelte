<script lang="ts">
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as I18n } from 'i18next';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	const i18n = getContext<Writable<I18n>>('i18n');
	export let show = false;
	export let provider = '';
</script>

<ConfirmDialog
	bind:show
	title={$i18n.t('Disconnect {{provider}}?', { provider: $i18n.t(provider) })}
	confirmLabel={$i18n.t('Disconnect')}
	on:confirm
	on:cancel
>
	<p class="text-sm text-gray-700 dark:text-gray-300">
		{$i18n.t(
			'Disconnect {{provider}}? Your synced folders stop updating and their files are removed from search. You can reconnect any time.',
			{ provider: $i18n.t(provider) }
		)}
	</p>
</ConfirmDialog>
