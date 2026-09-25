<script lang="ts">
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as I18nType } from 'i18next';
	import Folder from '$lib/components/icons/Folder.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';

	const i18n = getContext<Writable<I18nType>>('i18n');

	export let name: string;
	export let uploading: {
		total: number;
		uploaded: number;
		processed: number;
	} | null = null;
	export let selectionActive = false;
</script>

<div class="group flex w-full px-2 bg-transparent rounded-xl transition" role="listitem">
	{#if selectionActive}
		<div class="flex items-center" aria-hidden="true">
			<div class="p-1"><div class="size-3.5 shrink-0"></div></div>
		</div>
	{/if}
	<div class="flex items-center">
		<div class="p-1 rounded-full transition">
			<Folder className="size-3.5" />
		</div>
	</div>
	<div class="relative flex items-center gap-1 rounded-xl p-2 text-left flex-1 justify-between">
		<div>
			<div class="flex gap-2 items-center line-clamp-1">
				<div class="line-clamp-1 text-xs">{name}</div>
				{#if uploading}
					<span class="flex items-center gap-1 text-xs text-gray-400 shrink-0" role="status">
						&middot; {$i18n.t('Uploaded {{done}}/{{total}}', {
							done: uploading.uploaded,
							total: uploading.total
						})}
						&middot; {$i18n.t('Processed {{done}}/{{total}}', {
							done: uploading.processed,
							total: uploading.total
						})}
						<Spinner className="size-3" />
					</span>
				{/if}
			</div>
		</div>
	</div>
</div>
