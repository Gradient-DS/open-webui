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
	import GarbageBin from '$lib/components/icons/GarbageBin.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import VirtualList from '@sveltejs/svelte-virtual-list';
	import SelectCheckbox from './SelectCheckbox.svelte';
	import { fileItem, type KbSelection, type SelectableItem } from './selection';

	export let knowledge = null;
	export let selectedFileId = null;
	export let files = [];

	export let onClick = (fileId) => {};
	export let onDelete = (fileId) => {};

	// Optional multiselect model injected by KnowledgeBase. Null = no selection UI.
	export let selection: KbSelection | null = null;

	$: selectedStore = selection?.selected;
	$: selectionModeStore = selection?.selectionMode;

	const isSelectable = (file: any) => !!file?.id && file?.status !== 'uploading';
	const buildItem = (file: any): SelectableItem =>
		fileItem(file.id, file?.name ?? file?.meta?.name ?? '');

	$: orderedItems = (files ?? []).filter(isSelectable).map(buildItem);
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
</script>

<!--
	VirtualList fills the height of the parent scroll container (KnowledgeBase.svelte provides
	overflow-y-auto h-full). For short lists every row is visible so it behaves identically to a
	plain {#each}; for large lists only on-screen rows mount.
-->
<div class="h-full w-full">
	<VirtualList items={files} height="100%" let:item>
		{@const file = item}
		{@const selKey = `file:${file?.id}`}
		{@const isSel = (selection && isSelectable(file) && $selectedStore?.has(selKey)) ?? false}
		<!-- svelte-ignore a11y_no_static_element_interactions -->
		<div
			class=" group flex cursor-pointer w-full px-1.5 py-0.5 bg-transparent dark:hover:bg-gray-850/50 hover:bg-white rounded-xl transition {selection
				? 'select-none'
				: ''} {isSel
				? 'bg-blue-50 dark:bg-blue-900/20'
				: selectedFileId
					? ''
					: 'hover:bg-gray-100 dark:hover:bg-gray-850'}"
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
				{#if file?.status !== 'uploading'}
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
				{:else}
					<Spinner className="size-3.5" />
				{/if}
			</div>

			<button
				class="relative group flex items-center gap-1 rounded-xl p-2 text-left flex-1 justify-between"
				type="button"
				on:click={(e) => onRowClick(file, e)}
			>
				<div class="">
					<div class="flex gap-2 items-center line-clamp-1">
						<div class="line-clamp-1 text-sm">
							{file?.name ?? file?.meta?.name}
							{#if file?.meta?.size}
								<span class="text-xs text-gray-500">{formatFileSize(file?.meta?.size)}</span>
							{/if}
						</div>
					</div>
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
	</VirtualList>
</div>
