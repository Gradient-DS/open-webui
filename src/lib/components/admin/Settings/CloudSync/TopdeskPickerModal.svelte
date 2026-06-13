<script lang="ts">
	// ⚠️ STUB — Phase 3.1 placeholder. Phase 3.2 replaces this with the real
	// lazy TOPdesk tree picker (root items via `browseTopdeskItems(token)`,
	// children on expand via `browseTopdeskItems(token, parentId)`, folder/file
	// checkboxes, include-descendants subtree selection). For now this is just
	// enough to satisfy the picker contract SharedKbSection expects
	// (`bind:show`, `currentItems`, optional `title`/`confirmLabel`, dispatches
	// `select` with `{ items }`) so TopdeskSection compiles and the shared-KB
	// flow can be exercised end-to-end. Confirm forwards the existing selection
	// unchanged — there is no tree to pick from yet.

	import { getContext, createEventDispatcher } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	import Modal from '$lib/components/common/Modal.svelte';
	import type { TopdeskKbItem } from '$lib/apis/topdesk';

	const i18n = getContext<Writable<i18nType>>('i18n');
	const dispatch = createEventDispatcher<{ select: { items: TopdeskKbItem[] } }>();

	export let show = false;
	export let title: string | null = null;
	export let confirmLabel: string | null = null;
	// Items already opted into the shared KB. Forwarded verbatim on confirm
	// until the real tree picker (3.2) lets the admin change the selection.
	export let currentItems: TopdeskKbItem[] = [];

	const confirmHandler = () => {
		dispatch('select', { items: currentItems });
		show = false;
	};
</script>

<Modal bind:show size="sm">
	<div class="p-5 space-y-4">
		<div class="text-lg font-medium">{title ?? $i18n.t('Items to sync')}</div>

		<div class="text-sm text-gray-500 space-y-2">
			<p>{$i18n.t('The TOPdesk item picker is coming soon.')}</p>
			{#if currentItems.length}
				<p>
					{$i18n.t('Confirming will keep the current selection ({{count}} items).', {
						count: currentItems.length
					})}
				</p>
			{:else}
				<p>{$i18n.t('No items selected yet.')}</p>
			{/if}
		</div>

		<div class="flex justify-end gap-2 pt-2">
			<button
				type="button"
				class="px-3.5 py-1.5 text-sm rounded-lg bg-gray-50 hover:bg-gray-100 dark:bg-gray-850 dark:hover:bg-gray-800"
				on:click={() => (show = false)}
			>
				{$i18n.t('Cancel')}
			</button>
			<button
				type="button"
				class="px-3.5 py-1.5 text-sm font-medium rounded-lg bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100"
				on:click={confirmHandler}
			>
				{confirmLabel ?? $i18n.t('Provision')}
			</button>
		</div>
	</div>
</Modal>
