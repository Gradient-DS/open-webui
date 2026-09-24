<script lang="ts">
	import dayjs from '$lib/dayjs';
	import duration from 'dayjs/plugin/duration';
	import relativeTime from 'dayjs/plugin/relativeTime';

	dayjs.extend(duration);
	dayjs.extend(relativeTime);

	import { getContext } from 'svelte';
	const i18n = getContext('i18n');

	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import GarbageBin from '$lib/components/icons/GarbageBin.svelte';
	import Folder from '$lib/components/icons/Folder.svelte';
	import OneDrive from '$lib/components/icons/OneDrive.svelte';
	import GoogleDrive from '$lib/components/icons/GoogleDrive.svelte';
	import ExclamationTriangle from '$lib/components/icons/ExclamationTriangle.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import SelectCheckbox from './SelectCheckbox.svelte';
	import SourceControls from './SourceControls.svelte';
	import { folderBadge, type TreeStatusCounts } from '../utils/treeStatus';
	import type { SchedulePair } from '../utils/cloudSync';
	import { sourceState } from '../utils/sourceState';

	export let directory: {
		id: string;
		name: string;
		created_at: number;
		updated_at: number;
		child_count?: number;
		status_counts?: TreeStatusCounts;
		schedule_id?: string | null;
	};
	export let writeAccess = false;

	// [Gradient] Set when this directory is the root a cloud source writes: the
	// row shows the provider's logo and carries the source's sync controls.
	export let pair: SchedulePair | null = null;
	export let knowledgeId = '';
	export let syncAccess = false;
	export let syncBusy = false;
	export let isAdmin = false;
	// [Gradient] Set while a local folder upload is filling this directory.
	export let uploading: { done: number; total: number } | null = null;

	// Optional multiselect checkbox (dirs and source roots participate in bulk
	// delete). selectionActive renders the checkbox column (spacer when the row
	// itself is not selectable) so dir and file rows stay aligned.
	export let selectionActive = false;
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
	// When the row was last updated: the last finished sync for a source root,
	// the directory's own timestamp otherwise. Shown next to the file count.
	$: source = pair ? sourceState(pair, (pair.content ?? pair.acl)!.connection) : null;
	$: updatedAt = source
		? source.lastSyncedAt
		: directory.updated_at
			? new Date(directory.updated_at * 1000).toISOString()
			: null;
	let editing = false;
	let editName = '';
	let editInput: HTMLInputElement;
	let dragOver = false;

	const startRename = () => {
		editName = directory.name;
		editing = true;
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
	{#if selectionActive}
		<SelectCheckbox {selectable} {selected} visible={checkboxVisible} onToggle={onToggleSelect} />
	{/if}
	<div class="flex items-center">
		<button
			class="p-1 rounded-full transition"
			type="button"
			on:click={() => onNavigate(directory.id)}
		>
			<svelte:component
				this={pair?.content?.source_kind === 'onedrive' || pair?.acl?.source_kind === 'onedrive'
					? OneDrive
					: pair?.content?.source_kind === 'google_drive' ||
						  pair?.acl?.source_kind === 'google_drive'
						? GoogleDrive
						: Folder}
				className="size-3.5"
			/>
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
						class="text-xs w-full bg-transparent border-none outline-hidden"
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
					<div class="line-clamp-1 text-xs">
						{directory.name}
					</div>
				{/if}

				{#if (directory.child_count ?? null) !== null}
					<span class="text-xs text-gray-400 shrink-0">
						&middot; {$i18n.t('{{count}} files in folder', { count: directory.child_count })}
					</span>
				{/if}
				{#if uploading}
					<span class="flex items-center gap-1 text-xs text-gray-400 shrink-0" role="status">
						&middot; {$i18n.t('Uploading {{done}} of {{total}}', uploading)}
						<Spinner className="size-3" />
					</span>
				{:else if updatedAt}
					<Tooltip content={dayjs(updatedAt).format('LLLL')} className="shrink-0">
						<span class="text-xs text-gray-400">
							&middot; {$i18n.t('Updated {{time}}', { time: dayjs(updatedAt).fromNow() })}
						</span>
					</Tooltip>
				{:else if source && source.state !== 'syncing'}
					<span class="text-xs text-gray-400 shrink-0">&middot; {$i18n.t('Not synced yet')}</span>
				{/if}

				{#if uploading}
					<!-- the upload spinner above stands in for the processing one -->
				{:else if badge === 'failed'}
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
	</button>

	{#if pair}
		<SourceControls
			{knowledgeId}
			{pair}
			writeAccess={syncAccess}
			busy={syncBusy}
			{isAdmin}
			on:action
			on:reconnect
		/>
	{/if}

	{#if writeAccess}
		<div class="flex items-center">
			<Tooltip content={$i18n.t('Delete')}>
				<button
					class="p-1 rounded-full hover:bg-gray-100 dark:hover:bg-gray-850 transition"
					type="button"
					on:click={() => onDelete(directory.id)}
				>
					<GarbageBin className="size-3.5" />
				</button>
			</Tooltip>
		</div>
	{/if}
</div>
