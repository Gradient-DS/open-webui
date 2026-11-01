<script lang="ts">
	import { getContext } from 'svelte';
	import type { Readable } from 'svelte/store';
	import type { i18n as I18n } from 'i18next';

	const i18n = getContext<Readable<I18n>>('i18n');

	export let title: string | null | undefined = undefined;
</script>

{title || $i18n.t('New Chat')}
