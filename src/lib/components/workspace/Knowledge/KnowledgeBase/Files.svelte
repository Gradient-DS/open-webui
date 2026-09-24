<script lang="ts">
	import dayjs from '$lib/dayjs';
	import duration from 'dayjs/plugin/duration';
	import relativeTime from 'dayjs/plugin/relativeTime';

	dayjs.extend(duration);
	dayjs.extend(relativeTime);

	import { getContext, onDestroy } from 'svelte';
	const i18n = getContext('i18n');

	import { capitalizeFirstLetter, formatFileSize } from '$lib/utils';
	import { WEBUI_BASE_URL } from '$lib/constants';

	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import DocumentPage from '$lib/components/icons/DocumentPage.svelte';
	import ExclamationTriangle from '$lib/components/icons/ExclamationTriangle.svelte';
	import Folder from '$lib/components/icons/Folder.svelte';
	import GarbageBin from '$lib/components/icons/GarbageBin.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import DirectoryRow from './DirectoryRow.svelte';
	import SourceRow from './SourceRow.svelte';
	import SelectCheckbox from './SelectCheckbox.svelte';
	import { directoryItem, fileItem, type KbSelection, type SelectableItem } from './selection';
	import { breadcrumbSegments, fileBadge } from '../utils/treeStatus';
	import type { Connection, Schedule, ScheduleAction } from '$lib/apis/cloudSync';
	import type { SchedulePair } from '../utils/cloudSync';

	type KnowledgeFile = {
		id?: string;
		tempId?: string;
		itemId?: string;
		name?: string;
		status?: string;
		meta?: {
			name?: string;
			size?: number;
			warning?: string;
			relative_path?: string;
		};
		updated_at?: number;
		added_at?: number;
		error?: string;
		warning?: string;
		user?: {
			email?: string;
			name?: string;
		};
	};

	export let knowledge = null;
	export let selectedFileId = null;
	export let files: KnowledgeFile[] = [];
	export let directories = [];

	// [Gradient] Cloud sources live in the listing: a folder source is the
	// directory row its schedule writes (keyed by the row's schedule_id), a
	// single-file source gets its own row above the directories.
	export let sourcePairs: Map<string, SchedulePair> = new Map();
	export let looseSources: SchedulePair[] = [];
	export let syncAccess = false;
	export let syncBusy = false;
	export let isAdmin = false;
	export let onSourceAction: (
		schedules: Schedule[],
		action: ScheduleAction | 'delete'
	) => void = () => {};
	export let onReconnect: (connection: Connection) => void = () => {};

	// Search mode: flat KB-wide hits — directory rows hidden, each file row
	// shows its folder path (derived from meta.relative_path) instead.
	export let searchMode = false;
	// Gates every structure-write affordance: directory rename/delete menus
	// and drag-move of files/directories. See utils/structure.ts.
	export let structureEditable = false;

	export let onClick = (fileId) => {};
	export let onDelete = (fileId) => {};
	export let onNavigateDirectory = (directoryId: string) => {};
	export let onRenameDirectory = (id: string, name: string) => {};
	export let onDeleteDirectory = (id: string) => {};
	export let onMoveFilesToDirectory = (fileIds: string[], directoryId: string) => {};
	export let onMoveDirectoryToDirectory = (dirId: string, targetDirectoryId: string) => {};

	// Optional multiselect model injected by KnowledgeBase. Null = no selection UI.
	export let selection: KbSelection | null = null;

	$: selectedStore = selection?.selected;
	$: selectionModeStore = selection?.selectionMode;

	const isSelectable = (file: any) => !!file?.id && file?.status !== 'uploading';
	const buildItem = (file: any): SelectableItem =>
		fileItem(file.id, file?.name ?? file?.meta?.name ?? '');

	const buildDirItem = (dir: any): SelectableItem =>
		directoryItem(dir.id, dir.name, dir.child_count ?? 0);
	// Synced folders are removed through their own controls, never in bulk.
	const isDirSelectable = (dir: any) => !dir.schedule_id && structureEditable;

	// Selection order mirrors render order (dirs first, then files) so
	// Shift-range and drag-paint spans behave predictably.
	$: orderedItems = [
		...(searchMode ? [] : (directories ?? []).filter(isDirSelectable).map(buildDirItem)),
		...(files ?? []).filter(isSelectable).map(buildItem)
	];
	// Register this view's selectable rows so the header's select-all works.
	$: if (selection) selection.setAvailable(orderedItems);
	onDestroy(() => selection?.setAvailable([]));

	const onRowClick = (file: any, e: MouseEvent) => {
		if (selection && selection.consumeDidDrag()) return; // a drag just ended on this row
		if (selection && isSelectable(file) && (e.metaKey || e.ctrlKey || e.shiftKey)) {
			e.preventDefault();
			selection.select(buildItem(file), orderedItems, e);
			return;
		}
		// In selection mode (anything selected), a plain click toggles the row.
		if (selection && isSelectable(file) && selectionModeStore && $selectionModeStore) {
			selection.select(buildItem(file), orderedItems, e);
			return;
		}
		onClick(file?.id ?? file?.tempId);
	};

	const onRowPointerDown = (file: any) => {
		if (selection && isSelectable(file)) selection.pointerDown(buildItem(file), orderedItems);
	};
	const onRowPointerEnter = (file: any) => {
		if (selection && isSelectable(file)) selection.pointerEnter(buildItem(file));
	};

	// Native drag-to-move (files into directories / breadcrumbs): with no
	// selection, any row drags itself; once a selection exists, only SELECTED
	// rows are draggable and carry the whole selection — unselected rows stay
	// paint-select targets, so drag-paint multi-select keeps working.
	const dragPayloadIds = (file: any, isSel: boolean): string[] => {
		if (isSel && $selectedStore) {
			return [...$selectedStore.values()].filter((it) => it.kind === 'file').map((it) => it.fileId);
		}
		return file?.id ? [file.id] : [];
	};
</script>

<div class=" max-h-full flex flex-col w-full gap-[0.03125rem]" role="list">
	<!-- Sources and directories first -->
	{#if !searchMode}
		{#each looseSources as pair ((pair.content ?? pair.acl)?.id)}
			<SourceRow
				knowledgeId={knowledge?.id ?? ''}
				{pair}
				writeAccess={syncAccess}
				busy={syncBusy}
				{isAdmin}
				on:action={(event) => onSourceAction(event.detail.schedules, event.detail.action)}
				on:reconnect={(event) => onReconnect(event.detail)}
			/>
		{/each}
		{#each directories as dir (dir.id)}
			{@const dirSel = (selection && $selectedStore?.has(buildDirItem(dir).key)) ?? false}
			<DirectoryRow
				directory={dir}
				writeAccess={structureEditable}
				pair={dir.schedule_id ? (sourcePairs.get(dir.schedule_id) ?? null) : null}
				knowledgeId={knowledge?.id ?? ''}
				{syncAccess}
				{syncBusy}
				{isAdmin}
				on:action={(event) => onSourceAction(event.detail.schedules, event.detail.action)}
				on:reconnect={(event) => onReconnect(event.detail)}
				selectionActive={!!selection}
				selectable={!!(selection && isDirSelectable(dir))}
				selected={dirSel}
				checkboxVisible={!!$selectionModeStore}
				onToggleSelect={() => {
					if (selection && isDirSelectable(dir)) selection.toggle(buildDirItem(dir));
				}}
				onNavigate={(id) => onNavigateDirectory(id)}
				onRename={(id, name) => onRenameDirectory(id, name)}
				onDelete={(id) => onDeleteDirectory(id)}
				onFileDrop={(fileIds, directoryId) => onMoveFilesToDirectory(fileIds, directoryId)}
				onDirDrop={(dirId, targetId) => onMoveDirectoryToDirectory(dirId, targetId)}
			/>
		{/each}
	{/if}

	<!-- Files -->
	{#each files as file (file?.id ?? file?.itemId ?? file?.tempId)}
		{@const selKey = `file:${file?.id}`}
		{@const isSel = (selection && isSelectable(file) && $selectedStore?.has(selKey)) ?? false}
		{@const crumbs = searchMode ? breadcrumbSegments(file?.meta?.relative_path) : []}
		<div
			class=" group flex cursor-pointer w-full px-1.5 py-0.5 bg-transparent dark:hover:bg-gray-850/50 hover:bg-white rounded-xl transition {selection
				? 'select-none'
				: ''} {isSel
				? 'bg-blue-50 dark:bg-blue-900/20'
				: selectedFileId
					? ''
					: 'hover:bg-gray-100 dark:hover:bg-gray-850'}"
			role="listitem"
			draggable={structureEditable && !!file?.id && (!($selectionModeStore ?? false) || isSel)}
			on:dragstart={(e) => {
				if (!structureEditable) return;
				const ids = dragPayloadIds(file, isSel);
				if (ids.length) {
					e.dataTransfer?.setData('application/x-kb-file-move', JSON.stringify({ fileIds: ids }));
				}
			}}
			on:pointerdown={() => onRowPointerDown(file)}
			on:pointerenter={() => onRowPointerEnter(file)}
		>
			{#if selection}
				<SelectCheckbox
					selectable={isSelectable(file)}
					selected={isSel}
					visible={!!$selectionModeStore}
					onToggle={() => selection.toggle(buildItem(file))}
				/>
			{/if}
			<div class="flex items-center">
				{#if fileBadge(file?.status) === 'spinner'}
					<Spinner className="size-3.5" />
				{:else if fileBadge(file?.status) === 'error'}
					<Tooltip content={file?.error || $i18n.t('Processing error')}>
						<ExclamationTriangle className="size-3.5 text-red-500" />
					</Tooltip>
				{:else}
					<Tooltip content={$i18n.t('Download')}>
						<button
							class="p-1 rounded-full hover:bg-gray-100 dark:hover:bg-gray-850 transition"
							type="button"
							on:click={() => {
								let fileId = file?.id ?? file?.tempId;
								window.open(`${WEBUI_BASE_URL}/api/v1/files/${fileId}/content`, '_blank');
							}}
						>
							<DocumentPage className="size-3.5" />
						</button>
					</Tooltip>
				{/if}
			</div>

			<button
				class="relative group flex items-center gap-1 rounded-xl p-2 text-left flex-1 justify-between"
				type="button"
				on:click={(e) => onRowClick(file, e)}
			>
				<div class="min-w-0">
					<div class="flex gap-2 items-center line-clamp-1">
						{#if file?.status !== 'uploading' && (file?.warning ?? file?.meta?.warning)}
							<Tooltip content={$i18n.t('No searchable content could be extracted.')}>
								<ExclamationTriangle className="size-3.5 text-red-500 shrink-0" />
							</Tooltip>
						{/if}
						<div class="line-clamp-1 text-sm">
							{file?.name ?? file?.meta?.name}
							{#if file?.meta?.size}
								<span class="text-xs text-gray-500">{formatFileSize(file?.meta?.size)}</span>
							{/if}
						</div>
					</div>
					{#if crumbs.length}
						<div class="flex items-center gap-1 text-xs text-gray-400 mt-0.5 line-clamp-1">
							<Folder className="size-3 shrink-0" strokeWidth="2" />
							<span class="line-clamp-1">{crumbs.join(' / ')}</span>
						</div>
					{/if}
				</div>

				<div class="flex items-center gap-2 shrink-0">
					{#if file?.added_at || file?.updated_at}
						<Tooltip content={dayjs((file.added_at ?? file.updated_at) * 1000).format('LLLL')}>
							<div>
								{dayjs((file.added_at ?? file.updated_at) * 1000).fromNow()}
							</div>
						</Tooltip>
					{/if}

					{#if file?.user}
						<Tooltip
							content={file?.user?.email ?? $i18n.t('Deleted User')}
							className="flex shrink-0"
							placement="top-start"
						>
							<div class="shrink-0 text-gray-500">
								{$i18n.t('By {{name}}', {
									name: capitalizeFirstLetter(
										file?.user?.name ?? file?.user?.email ?? $i18n.t('Deleted User')
									)
								})}
							</div>
						</Tooltip>
					{/if}
				</div>
			</button>

			{#if knowledge?.write_access}
				<div class="flex items-center">
					<Tooltip content={$i18n.t('Delete')}>
						<button
							class="p-1 rounded-full hover:bg-gray-100 dark:hover:bg-gray-850 transition"
							type="button"
							on:click={() => {
								onDelete(file?.id ?? file?.tempId);
							}}
						>
							<GarbageBin className="size-3.5" />
						</button>
					</Tooltip>
				</div>
			{/if}
		</div>
	{/each}
</div>
