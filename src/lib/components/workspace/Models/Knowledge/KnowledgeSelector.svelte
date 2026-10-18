<script lang="ts">
	import { enabledProviders, loadSourcePolicy } from '$lib/sources/policy';
	import { onMount, onDestroy, getContext, createEventDispatcher, tick } from 'svelte';
	import { searchNotes } from '$lib/apis/notes';
	import { searchKnowledgeBases, searchKnowledgeFiles } from '$lib/apis/knowledge';

	import { decodeString } from '$lib/utils';

	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import Search from '$lib/components/icons/Search.svelte';
	import SourceItemIcon from '$lib/components/common/SourceItemIcon.svelte';
	import PageEdit from '$lib/components/icons/PageEdit.svelte';
	import DocumentPage from '$lib/components/icons/DocumentPage.svelte';
	import FolderOpen from '$lib/components/icons/FolderOpen.svelte';

	const i18n = getContext<typeof import('$lib/i18n').default>('i18n');
	const dispatch = createEventDispatcher();

	export let onClose: () => void = () => {};
	// [Gradient] When true, show a "New Knowledge" footer that creates a KB in-flow,
	// offering the same type/cloud-sync choices as the workspace flow.
	export let allowCreate = false;

	export let show = false;
	export let anchorElement: HTMLElement | null = null;
	export let selectedItems = [];
	let dropdown: Dropdown;
	export const close = () => dropdown.close();
	export let disabled = false;
	let inputElement: HTMLInputElement | null = null;

	$: if (disabled && show) show = false;
	let query = '';
	let searchDebounceTimer: ReturnType<typeof setTimeout>;

	let noteItems = [];
	let knowledgeItems = [];
	let fileItems = [];

	let items = [];

	$: items = [...noteItems, ...knowledgeItems, ...fileItems].filter(
		(item) =>
			!selectedItems.some((selected) => selected.id === item.id && selected.type === item.type)
	);

	const handleSearchInput = () => {
		clearTimeout(searchDebounceTimer);
		searchDebounceTimer = setTimeout(() => {
			getItems();
		}, 300);
	};

	onDestroy(() => {
		clearTimeout(searchDebounceTimer);
	});

	const getItems = () => {
		getNoteItems();
		getKnowledgeItems();
		getKnowledgeFileItems();
	};

	const getNoteItems = async () => {
		const res = await searchNotes(localStorage.token, query).catch(() => {
			return null;
		});

		if (res) {
			noteItems = res.items.map((note) => {
				return {
					...note,
					type: 'note',
					name: note.title
				};
			});
		}
	};

	const getKnowledgeItems = async () => {
		const res = await searchKnowledgeBases(localStorage.token, query).catch(() => {
			return null;
		});

		if (res) {
			knowledgeItems = res.items.map((note) => {
				return {
					...note,
					knowledge_type: note.type,
					type: 'collection'
				};
			});
		}
	};

	const getKnowledgeFileItems = async () => {
		const res = await searchKnowledgeFiles(localStorage.token, query).catch(() => {
			return null;
		});

		if (res) {
			fileItems = res.items.map((file) => {
				return {
					...file,
					type: 'file',
					name: file.meta?.name || file.filename,
					description: file.description || ''
				};
			});
		}
	};

	onMount(async () => {
		void loadSourcePolicy(localStorage.token);
		getItems();
	});
</script>

<Dropdown
	bind:this={dropdown}
	{anchorElement}
	bind:show
	onOpenChange={async (state) => {
		if (disabled) {
			show = false;
			return;
		}
		if (state) {
			await tick();
			inputElement?.focus();
		} else {
			onClose();
			query = '';
			handleSearchInput();
		}
	}}
>
	<slot />
	<div
		slot="content"
		class="flex w-96 max-w-[calc(100vw-2rem)] flex-col rounded-xl border border-gray-200 bg-white p-1 text-gray-900 shadow-lg dark:border-gray-800 dark:bg-gray-850 dark:text-gray-100"
	>
		<div class="flex items-center gap-2 px-2 py-1">
			<Search className="size-3.5 text-gray-500" />
			<input
				bind:this={inputElement}
				bind:value={query}
				on:input={handleSearchInput}
				class="min-w-0 w-full bg-transparent py-0.5 text-xs placeholder:text-gray-500 dark:placeholder:text-gray-400 outline-hidden"
				aria-label={$i18n.t('Search knowledge')}
				placeholder={$i18n.t('Search knowledge')}
			/>
		</div>
		<div class="max-h-72 overflow-y-auto">
			{#if selectedItems.length}
				<div class="px-2 py-1 text-[0.6875rem] text-gray-500 dark:text-gray-400">
					{$i18n.t('Selected')}
				</div>
				<slot name="selected" />
			{/if}
			{#if items.length === 0 && (query || !selectedItems.length)}
				<div class="px-3 py-4 text-xs leading-5 text-gray-500 dark:text-gray-400">
					{$i18n.t('No knowledge found')}
					{#if !query}<p>
							{$i18n.t('Upload files or add sources in the Knowledge workspace.')}
						</p>{/if}
				</div>
			{:else}
				{#each items as item, i}
					{#if i === 0 || item.type !== items[i - 1].type}
						<div class="px-2 py-1 text-[0.6875rem] text-gray-500 dark:text-gray-400">
							{item.type === 'note'
								? $i18n.t('Notes')
								: item.type === 'collection'
									? $i18n.t('Collections')
									: $i18n.t('Files')}
						</div>
					{/if}
					<button
						type="button"
						{disabled}
						class="flex min-h-7 w-full items-center gap-2 rounded-xl px-2 py-1 text-left text-xs hover:bg-gray-50 dark:hover:bg-gray-800"
						on:click={() => {
							if (!disabled) dispatch('select', item);
							inputElement?.focus();
						}}
					>
						<span class="shrink-0 text-gray-500">
							{#if item.type === 'note'}<PageEdit
									className="size-3.5"
								/>{:else if item.type === 'collection'}<SourceItemIcon
									kind="folder"
									icon={FolderOpen}
									provider={item.knowledge_type}
								/>{:else}<DocumentPage className="size-3.5" />{/if}
						</span>
						<span class="truncate">{decodeString(item.name)}</span>
					</button>
				{/each}
			{/if}
		</div>

		{#if allowCreate}
			<!-- [Gradient] Create a KB in-flow (simple assistant builder). -->
			<div class="mt-1 border-t border-gray-100 pt-1 dark:border-gray-800">
				<div class="px-2 py-1 text-[0.6875rem] text-gray-500 dark:text-gray-400">
					{$i18n.t('New Knowledge')}
				</div>
				<button
					class="flex min-h-7 w-full items-center gap-2 rounded-xl px-2 py-1 text-left text-xs hover:bg-gray-50 dark:hover:bg-gray-800"
					type="button"
					{disabled}
					on:click={() => {
						dispatch('create', 'local');
						show = false;
					}}
				>
					<FolderOpen className="size-3.5" strokeWidth="2" />
					<span class="truncate">{$i18n.t('Local Knowledge Base')}</span>
				</button>

				{#each $enabledProviders as provider}
					<button
						class="flex min-h-7 w-full items-center gap-2 rounded-xl px-2 py-1 text-left text-xs hover:bg-gray-50 dark:hover:bg-gray-800"
						type="button"
						{disabled}
						on:click={() => {
							dispatch('create', provider.kind);
							show = false;
						}}
					>
						<svelte:component this={provider.icon} className="size-3.5" />
						<span class="truncate">{$i18n.t('From {{label}}', { label: provider.label })}</span>
					</button>
				{/each}
			</div>
		{/if}
		<div
			class="mt-1 flex items-center justify-between gap-3 px-2 py-1.5 text-xs text-gray-500 dark:text-gray-400"
		>
			<slot name="actions" />
		</div>
	</div>
</Dropdown>
