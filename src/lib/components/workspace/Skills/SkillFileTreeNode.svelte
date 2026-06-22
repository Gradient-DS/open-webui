<script lang="ts">
	import dayjs from '$lib/dayjs';
	import { getContext } from 'svelte';
	import { slide } from 'svelte/transition';
	import { quintOut } from 'svelte/easing';

	import { formatFileSize } from '$lib/utils';
	import type { SkillTreeNode } from './utils';

	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import DocumentPage from '$lib/components/icons/DocumentPage.svelte';
	import Folder from '$lib/components/icons/Folder.svelte';
	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import ChevronRight from '$lib/components/icons/ChevronRight.svelte';
	import EllipsisHorizontal from '$lib/components/icons/EllipsisHorizontal.svelte';
	import PagePlus from '$lib/components/icons/PagePlus.svelte';
	import NewFolderAlt from '$lib/components/icons/NewFolderAlt.svelte';
	import ArrowUpTray from '$lib/components/icons/ArrowUpTray.svelte';
	import Pencil from '$lib/components/icons/Pencil.svelte';
	import GarbageBin from '$lib/components/icons/GarbageBin.svelte';

	const i18n = getContext('i18n');

	export let node: SkillTreeNode;
	export let expandedKey: string;
	export let expandedSources: Record<string, boolean>;
	export let disabled = false;

	// Action callbacks (path here is the folder prefix the action targets, '' = root)
	export let onOpenFile: (file: { path: string }) => void;
	export let onNewFile: (folderPrefix: string) => void;
	export let onNewFolder: (folderPrefix: string) => void;
	export let onUpload: (folderPrefix: string) => void;
	export let onRenameFile: (file: { path: string; name: string }) => void;
	export let onDeleteFile: (file: { path: string; name: string }) => void;
	export let onRenameFolder: (folderPrefix: string) => void;
	export let onDeleteFolder: (folderPrefix: string) => void;

	// The drag-hovered folder prefix (bound from the root for highlight + drop target)
	export let dragOverPrefix: string | null = null;

	$: folderKey = `${expandedKey}/${node.path}`;
	$: expanded = expandedSources[folderKey] ?? false;

	let menuShow = false;

	const toggle = () => {
		expandedSources[folderKey] = !expanded;
	};
</script>

{#if node.path !== ''}
	<!-- Folder header (root has no header, only contents) -->
	<div
		class="group flex items-center w-full px-1.5 py-0.5 rounded-xl transition {dragOverPrefix ===
		node.path
			? 'bg-gray-100 dark:bg-gray-850'
			: 'hover:bg-gray-50 dark:hover:bg-gray-850/50'}"
		data-folder-prefix={node.path}
	>
		<button
			class="flex items-center gap-1.5 flex-1 p-1.5 text-left text-sm text-gray-500 min-w-0"
			type="button"
			on:click={toggle}
		>
			<div class="shrink-0">
				{#if expanded}
					<ChevronDown className="size-3" strokeWidth="2.5" />
				{:else}
					<ChevronRight className="size-3" strokeWidth="2.5" />
				{/if}
			</div>
			<div class="shrink-0">
				<Folder className="size-3" strokeWidth="2" />
			</div>
			<span class="line-clamp-1 text-xs font-medium">{node.name}</span>
		</button>

		{#if !disabled}
			<button
				class="shrink-0 invisible group-hover:visible self-center flex items-center text-gray-400 dark:text-gray-300"
				type="button"
			>
				<Dropdown bind:show={menuShow} align="end">
					<div class="p-1 hover:bg-gray-100 dark:hover:bg-gray-850 rounded-lg">
						<EllipsisHorizontal className="size-4" strokeWidth="2.5" />
					</div>

					<div slot="content">
						<div
							class="min-w-[180px] rounded-2xl px-1 py-1 border border-gray-100 dark:border-gray-800 z-50 bg-white dark:bg-gray-850 dark:text-white shadow-lg"
						>
							<button
								class="flex gap-2 items-center px-3 py-1.5 text-sm select-none cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl w-full"
								type="button"
								on:click={() => onNewFile(node.path)}
							>
								<PagePlus className="size-4" />
								<div class="flex items-center">{$i18n.t('New file')}</div>
							</button>
							<button
								class="flex gap-2 items-center px-3 py-1.5 text-sm select-none cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl w-full"
								type="button"
								on:click={() => onNewFolder(node.path)}
							>
								<NewFolderAlt className="size-4" />
								<div class="flex items-center">{$i18n.t('New folder')}</div>
							</button>
							<button
								class="flex gap-2 items-center px-3 py-1.5 text-sm select-none cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl w-full"
								type="button"
								on:click={() => onUpload(node.path)}
							>
								<ArrowUpTray className="size-4" />
								<div class="flex items-center">{$i18n.t('Upload here')}</div>
							</button>

							<hr class="border-gray-50/30 dark:border-gray-800/30 my-1" />

							<button
								class="flex gap-2 items-center px-3 py-1.5 text-sm select-none cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl w-full"
								type="button"
								on:click={() => onRenameFolder(node.path)}
							>
								<Pencil className="size-4" />
								<div class="flex items-center">{$i18n.t('Rename')}</div>
							</button>
							<button
								class="flex gap-2 items-center px-3 py-1.5 text-sm select-none cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl w-full"
								type="button"
								on:click={() => onDeleteFolder(node.path)}
							>
								<GarbageBin className="size-4" />
								<div class="flex items-center">{$i18n.t('Delete')}</div>
							</button>
						</div>
					</div>
				</Dropdown>
			</button>
		{/if}
	</div>
{/if}

{#if node.path === '' || expanded}
	<div transition:slide={{ duration: 200, easing: quintOut, axis: 'y' }}>
		<div class={node.path === '' ? '' : 'ml-3 pl-1 border-s border-gray-100 dark:border-gray-900'}>
			{#each node.children as child (child.path)}
				<svelte:self
					node={child}
					{expandedKey}
					bind:expandedSources
					{disabled}
					{onOpenFile}
					{onNewFile}
					{onNewFolder}
					{onUpload}
					{onRenameFile}
					{onDeleteFile}
					{onRenameFolder}
					{onDeleteFolder}
					bind:dragOverPrefix
				/>
			{/each}

			{#if node.pending && node.files.length === 0 && node.children.length === 0}
				<div class="px-3 py-1 text-xs text-gray-400 dark:text-gray-500 italic">
					{$i18n.t('Empty folder — add a file to keep it.')}
				</div>
			{/if}

			{#each node.files as file (file.path)}
				<div
					class="group flex items-center w-full px-1.5 py-0.5 hover:bg-gray-50 dark:hover:bg-gray-850/50 rounded-xl transition"
				>
					<button
						class="flex items-center gap-1.5 flex-1 p-1.5 text-left text-gray-500 min-w-0"
						type="button"
						on:click={() => onOpenFile(file)}
					>
						<div class="shrink-0">
							<DocumentPage className="size-3" />
						</div>
						<span class="line-clamp-1 text-xs">{file.name}</span>
						{#if file?.meta?.size}
							<span class="text-gray-400 text-xs shrink-0">
								{formatFileSize(file.meta.size)}
							</span>
						{/if}
						{#if file?.updated_at}
							<Tooltip content={dayjs(file.updated_at * 1000).format('LLLL')}>
								<span class="text-gray-400 text-xs shrink-0">
									{dayjs(file.updated_at * 1000).fromNow()}
								</span>
							</Tooltip>
						{/if}
					</button>

					{#if !disabled}
						<div class="flex items-center gap-0.5 shrink-0 invisible group-hover:visible">
							<button
								class="p-1 text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 rounded-lg transition"
								type="button"
								aria-label={$i18n.t('Rename')}
								on:click|stopPropagation={() => onRenameFile(file)}
							>
								<Pencil className="size-3.5" />
							</button>
							<button
								class="p-1 text-gray-400 hover:text-red-500 dark:hover:text-red-400 rounded-lg transition"
								type="button"
								aria-label={$i18n.t('Delete')}
								on:click|stopPropagation={() => onDeleteFile(file)}
							>
								<GarbageBin className="size-3.5" />
							</button>
						</div>
					{/if}
				</div>
			{/each}
		</div>
	</div>
{/if}
