<script lang="ts">
	import { getContext } from 'svelte';
	import { accessUpdateState } from './utils/accessUpdate';
	const i18n = getContext<import('svelte/store').Writable<import('i18next').i18n>>('i18n');
	export let meta: Record<string, unknown> | undefined = undefined;
	$: state = accessUpdateState(meta);
</script>

{#if state}
	<span role="status" class="text-xs text-gray-500 dark:text-gray-400">
		{state === 'pending'
			? $i18n.t('Access changes are being applied. Showing current access.')
			: $i18n.t('Access changes failed. Showing current access.')}
	</span>
{/if}
