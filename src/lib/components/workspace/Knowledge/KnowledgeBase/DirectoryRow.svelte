<script lang="ts">
	import dayjs from '$lib/dayjs';
	import duration from 'dayjs/plugin/duration';
	import relativeTime from 'dayjs/plugin/relativeTime';

	dayjs.extend(duration);
	dayjs.extend(relativeTime);

	import { getContext } from 'svelte';
	const i18n = getContext('i18n');

	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import GarbageBin from '$lib/components/icons/GarbageBin.svelte';
	import Pencil from '$lib/components/icons/Pencil.svelte';
	import Folder from '$lib/components/icons/Folder.svelte';
	import EllipsisHorizontal from '$lib/components/icons/EllipsisHorizontal.svelte';
	import ExclamationTriangle from '$lib/components/icons/ExclamationTriangle.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import SelectCheckbox from './SelectCheckbox.svelte';
	import { folderBadge, type TreeStatusCounts } from '../utils/treeStatus';

	export let directory: {
		id: string;
		name: string;
		created_at: number;
		updated_at: number;
		child_count?: number;
		status_counts?: TreeStatusCounts;
	};
	export let writeAccess = false;

	// Cloud chrome (Phase 3): set when this directory is a provider source's
	// materialized root (sources[].root_directory_id) — adds the sync spinner
	// and the remove-source affordance.
	export let source: { itemId: string; name: string } | null = null;
	export let isSyncing = false;
	export let onRemoveSource: ((itemId: string, name: string) => void) | null = null;

	// Optional multiselect checkbox (source roots participate in bulk delete).
	export let selectable = false;
	export let selected = false;
	export let checkboxVisible = false;
	export let onToggleSelect: () => void = () => {};

	export let onNavigate: (id: string) => void = () => {};
	export let onRename: (id: string, name: string) => void = () => {};
	export let onDelete: (id: string) => void = () => {};
	export let onFileDrop: (fileIds: string[], directoryId: string) => void = () => {};
	export let onDirDrop: (dirId: string, targetDirectoryId: string) => void = () => {};

	$: badge = folderBadge(directory.status_counts);
	let editing = false;
	let editName = '';
	let editInput: HTMLInputElement;
	let dragOver = false;
	let showDropdown = false;

	const startRename = () => {
		editName = directory.name;
		editing = true;
		showDropdown = false;
		setTimeout(() => editInput?.select(), 0);
	};

	const submitRename = () => {
		if (!editName.trim() || editName === directory.name) {
			editing = false;
			return;
		}
		onRename(directory.id, editName.trim());
		editing = false;
	};

	const cancelRename = () => {
		editing = false;
	};
</script>

<!-- svelte-ignore a11y-no-static-element-interactions -->
<div
	class="group flex cursor-pointer w-full px-2 bg-transparent dark:hover:bg-gray-850/50 hover:bg-white rounded-xl transition
		{dragOver
		? 'bg-gray-100 dark:bg-gray-800 ring-1 ring-gray-300 dark:ring-gray-600'
		: 'hover:bg-gray-100 dark:hover:bg-gray-850'}"
	draggable={writeAccess}
	on:dragstart={(e) => {
		if (!writeAccess) return;
		e.dataTransfer?.setData('application/x-kb-dir-move', JSON.stringify({ dirId: directory.id }));
	}}
	on:dblclick={() => {
		if (writeAccess) startRename();
	}}
	on:dragover={(e) => {
		const hasFile = e.dataTransfer?.types.includes('application/x-kb-file-move');
		const hasDir = e.dataTransfer?.types.includes('application/x-kb-dir-move');
		if (!hasFile && !hasDir) return;
		e.preventDefault();
		e.stopPropagation();
		dragOver = true;
	}}
	on:dragleave={() => {
		dragOver = false;
	}}
	on:drop={(e) => {
		e.preventDefault();
		e.stopPropagation();
		dragOver = false;
		const fileRaw = e.dataTransfer?.getData('application/x-kb-file-move');
		if (fileRaw) {
			try {
				const data = JSON.parse(fileRaw);
				const fileIds = data.fileIds ?? (data.fileId ? [data.fileId] : []);
				if (fileIds.length) {
					onFileDrop(fileIds, directory.id);
				}
			} catch {}
			return;
		}
		const dirRaw = e.dataTransfer?.getData('application/x-kb-dir-move');
		if (dirRaw) {
			try {
				const data = JSON.parse(dirRaw);
				if (data.dirId !== directory.id) {
					onDirDrop(data.dirId, directory.id);
				}
			} catch {}
		}
	}}
>
	{#if selectable}
		<SelectCheckbox
			selectable={true}
			{selected}
			visible={checkboxVisible}
			onToggle={onToggleSelect}
		/>
	{/if}
	<div class="flex items-center">
		<button
			class="p-1 rounded-full transition"
			type="button"
			on:click={() => onNavigate(directory.id)}
		>
			<Folder className="size-3.5" />
		</button>
	</div>

	<button
		class="relative flex items-center gap-1 rounded-xl p-2 text-left flex-1 justify-between"
		type="button"
		on:click={() => {
			if (editing) return;
			onNavigate(directory.id);
		}}
	>
		<div>
			<div class="flex gap-2 items-center line-clamp-1">
				{#if editing}
					<!-- svelte-ignore a11y-autofocus -->
					<input
						bind:this={editInput}
						bind:value={editName}
						class="text-sm w-full bg-transparent border-none outline-hidden"
						on:keydown={(e) => {
							if (e.key === 'Enter') submitRename();
							if (e.key === 'Escape') cancelRename();
							if (e.key === ' ') e.stopPropagation();
						}}
						on:keyup={(e) => {
							if (e.key === ' ') e.stopPropagation();
						}}
						on:blur={submitRename}
						on:click={(e) => e.stopPropagation()}
						autofocus
					/>
				{:else}
					<div class="line-clamp-1 text-sm">
						{directory.name}
					</div>
				{/if}

				{#if source && isSyncing}
					<span class="text-xs text-gray-400 shrink-0">&middot;</span>
					<Spinner className="size-3" />
				{/if}

				{#if (directory.child_count ?? null) !== null}
					<span class="text-xs text-gray-400 shrink-0">
						&middot; {$i18n.t('{{count}} files in folder', { count: directory.child_count })}
					</span>
				{/if}

				{#if badge === 'failed'}
					<Tooltip
						content={$i18n.t('{{count}} failed', { count: directory.status_counts?.failed ?? 0 })}
					>
						<ExclamationTriangle className="size-3 text-red-500 shrink-0" />
					</Tooltip>
				{:else if badge === 'pending'}
					<Spinner className="size-3" />
				{/if}
			</div>
		</div>

		<div class="flex items-center gap-2 shrink-0">
			{#if directory.updated_at}
				<Tooltip content={dayjs(directory.updated_at * 1000).format('LLLL')}>
					<div class="text-xs text-gray-400">
						{dayjs(directory.updated_at * 1000).fromNow()}
					</div>
				</Tooltip>
			{/if}
		</div>
	</button>

	{#if source && onRemoveSource}
		<div class="flex items-center opacity-0 group-hover:opacity-100 transition-opacity">
			<Tooltip content={$i18n.t('Remove Source')}>
				<button
					class="p-1 rounded-full hover:bg-gray-100 dark:hover:bg-gray-850 transition"
					type="button"
					on:click={() => onRemoveSource(source.itemId, source.name)}
				>
					<GarbageBin className="size-3.5" />
				</button>
			</Tooltip>
		</div>
	{/if}

	{#if writeAccess}
		<div class="flex items-center">
			<Dropdown bind:show={showDropdown} align="end" sideOffset={4}>
				<button
					class="p-1 rounded-full hover:bg-gray-100 dark:hover:bg-gray-850 transition"
					type="button"
				>
					<EllipsisHorizontal className="size-3.5" />
				</button>

				<div slot="content">
					<div
						class="min-w-[140px] rounded-2xl p-1 z-[9999999] bg-white dark:bg-gray-850 dark:text-white shadow-lg border border-gray-100 dark:border-gray-800"
					>
						<button
							type="button"
							class="select-none flex rounded-xl py-1.5 px-3 w-full hover:bg-gray-50 dark:hover:bg-gray-800 transition items-center gap-2 text-sm"
							on:click={() => startRename()}
						>
							<Pencil className="size-3.5" />
							{$i18n.t('Rename')}
						</button>
						<button
							type="button"
							class="select-none flex rounded-xl py-1.5 px-3 w-full hover:bg-gray-50 dark:hover:bg-gray-800 transition items-center gap-2 text-sm"
							on:click={() => onDelete(directory.id)}
						>
							<GarbageBin className="size-3.5" />
							{$i18n.t('Delete')}
						</button>
					</div>
				</div>
			</Dropdown>
		</div>
	{/if}
</div>
