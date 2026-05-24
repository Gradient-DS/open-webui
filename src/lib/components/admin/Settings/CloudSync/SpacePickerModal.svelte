<script lang="ts">
	import { getContext, createEventDispatcher } from 'svelte';

	import {
		getConfluenceSharedKbSpaces,
		type ConfluenceSharedKbSpace
	} from '$lib/apis/confluence';
	import Modal from '$lib/components/common/Modal.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';

	const i18n = getContext('i18n');
	const dispatch = createEventDispatcher<{ confirm: { spaces: ConfluenceSharedKbSpace[] } }>();

	export let show = false;
	// Space ids currently opted into the shared KB — pre-ticks the list.
	export let currentSpaceIds: string[] = [];

	let availableSpaces: ConfluenceSharedKbSpace[] = [];
	let selectedSpaceIds: string[] = [];
	let loading = false;
	let error = '';
	let query = '';
	let initialized = false;

	// Re-initialise each time the modal opens: reset the selection to the
	// stored one and reload the live space catalog from the service account.
	$: if (show && !initialized) {
		initialized = true;
		init();
	}
	$: if (!show) {
		initialized = false;
	}

	const init = async () => {
		selectedSpaceIds = [...currentSpaceIds];
		query = '';
		error = '';
		loading = true;
		try {
			const res = await getConfluenceSharedKbSpaces(localStorage.token);
			availableSpaces = [...res.spaces].sort((a, b) =>
				(a.name ?? '').localeCompare(b.name ?? '')
			);
		} catch (err) {
			error = `${err}`;
			availableSpaces = [];
		}
		loading = false;
	};

	$: filteredSpaces = query.trim()
		? availableSpaces.filter((s) =>
				`${s.name ?? ''} ${s.key ?? ''}`.toLowerCase().includes(query.trim().toLowerCase())
			)
		: availableSpaces;

	const toggleSpace = (id: string) => {
		selectedSpaceIds = selectedSpaceIds.includes(id)
			? selectedSpaceIds.filter((x) => x !== id)
			: [...selectedSpaceIds, id];
	};

	const selectAll = () => {
		selectedSpaceIds = availableSpaces.map((s) => s.id);
	};

	const clearAll = () => {
		selectedSpaceIds = [];
	};

	const confirm = () => {
		dispatch('confirm', {
			spaces: availableSpaces.filter((s) => selectedSpaceIds.includes(s.id))
		});
		show = false;
	};
</script>

<Modal bind:show size="lg">
	<div class="text-sm">
		<div class="flex justify-between items-center px-5 pt-4 pb-1">
			<div class="text-lg font-medium">{$i18n.t('Spaces to sync')}</div>
			<button class="self-center" on:click={() => (show = false)}>
				<XMark className="size-5" />
			</button>
		</div>

		<div class="px-5 pb-4">
			<div class="text-xs text-gray-500 mb-3">
				{$i18n.t('Pick which Confluence spaces sync into the shared knowledge base.')}
			</div>

			{#if loading}
				<div class="flex justify-center py-10"><Spinner /></div>
			{:else if error}
				<div class="text-sm text-red-500 py-4">{error}</div>
			{:else}
				<input
					class="w-full text-sm bg-transparent outline-hidden border-b border-gray-100 dark:border-gray-850 pb-1 mb-2"
					type="text"
					bind:value={query}
					placeholder={$i18n.t('Search spaces')}
				/>
				<div class="flex gap-2 text-xs text-gray-500 mb-2">
					<button type="button" class="hover:underline" on:click={selectAll}>
						{$i18n.t('Select all')}
					</button>
					<span>·</span>
					<button type="button" class="hover:underline" on:click={clearAll}>
						{$i18n.t('Clear selection')}
					</button>
					<span class="ml-auto">{selectedSpaceIds.length} / {availableSpaces.length}</span>
				</div>

				<div
					class="max-h-96 overflow-y-auto rounded-lg bg-gray-50 dark:bg-gray-850 p-2 space-y-0.5"
				>
					{#each filteredSpaces as space (space.id)}
						<label class="flex items-center gap-2 cursor-pointer py-1 px-1">
							<input
								type="checkbox"
								checked={selectedSpaceIds.includes(space.id)}
								on:change={() => toggleSpace(space.id)}
							/>
							<span>{space.name || space.key || space.id}</span>
							{#if space.key}
								<span class="text-xs text-gray-400">({space.key})</span>
							{/if}
						</label>
					{:else}
						<div class="text-xs text-gray-500 py-4 text-center">
							{availableSpaces.length
								? $i18n.t('No spaces match your search.')
								: $i18n.t('No Confluence spaces are available.')}
						</div>
					{/each}
				</div>

				<!-- Always rendered with a reserved height so toggling the
				     warning does not resize the modal as spaces are picked. -->
				<div class="mt-2 text-xs text-gray-500 min-h-5">
					{#if selectedSpaceIds.length === 0}
						{$i18n.t(
							'No spaces selected — the shared knowledge base stays empty until you pick at least one.'
						)}
					{/if}
				</div>
			{/if}
		</div>

		<div class="flex justify-end gap-2 px-5 pb-4">
			<button
				type="button"
				class="px-3.5 py-1.5 text-sm rounded-full bg-gray-50 hover:bg-gray-100 dark:bg-gray-850 dark:hover:bg-gray-800"
				on:click={() => (show = false)}
			>
				{$i18n.t('Cancel')}
			</button>
			<button
				type="button"
				class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 rounded-full disabled:opacity-50 disabled:cursor-not-allowed"
				on:click={confirm}
				disabled={loading || !!error}
			>
				{$i18n.t('Provision')}
			</button>
		</div>
	</div>
</Modal>
