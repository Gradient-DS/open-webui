<script lang="ts">
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as I18n } from 'i18next';
	import ExclamationTriangle from '$lib/components/icons/ExclamationTriangle.svelte';

	const i18n = getContext<Writable<I18n>>('i18n');
	let {
		items = []
	}: { items?: { type: string; id: string; name: string; reason: 'cloud' | 'owner' }[] } = $props();
</script>

{#if items.length > 0}
	<div
		class="mb-3 flex gap-2 rounded-xl border border-yellow-500/30 bg-yellow-500/10 px-3 py-2 text-xs text-yellow-800 dark:text-yellow-200"
	>
		<ExclamationTriangle className="size-4 shrink-0 mt-0.5" />
		<div>
			<div>{$i18n.t('These items stay private:')}</div>
			<ul class="mt-1 list-disc pl-4">
				{#each items as item (item.type + item.id)}
					<li>
						<span class="font-medium">{item.name}</span>:
						{item.reason === 'cloud'
							? $i18n.t('synced from the cloud, never shared')
							: $i18n.t("someone else's, only its owner can share it")}
					</li>
				{/each}
			</ul>
		</div>
	</div>
{/if}
