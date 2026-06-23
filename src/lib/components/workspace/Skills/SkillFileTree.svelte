<script lang="ts">
	import { getContext, onMount, onDestroy, tick } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { toast } from 'svelte-sonner';

	import { uploadFile } from '$lib/apis/files';
	import {
		getSkillFileList,
		getSkillFileContentBlob,
		createSkillFile,
		createSkillFileInline,
		updateSkillFileContent,
		moveSkillFile,
		removeSkillFilePath
	} from '$lib/apis/skills';

	import { formatFileSize } from '$lib/utils';
	import {
		buildTree,
		isTextPath,
		languageForPath,
		type SkillFileItem,
		type SkillTreeNode
	} from './utils';

	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import CodeEditor from '$lib/components/common/CodeEditor.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import SkillFileTreeNode from './SkillFileTreeNode.svelte';
	import PagePlus from '$lib/components/icons/PagePlus.svelte';
	import NewFolderAlt from '$lib/components/icons/NewFolderAlt.svelte';
	import ArrowUpTray from '$lib/components/icons/ArrowUpTray.svelte';
	import ArrowDownTray from '$lib/components/icons/ArrowDownTray.svelte';
	import DocumentPage from '$lib/components/icons/DocumentPage.svelte';

	export let skillId: string;
	export let disabled = false;

	const i18n = getContext<Writable<i18nType>>('i18n');

	let fileItems: SkillFileItem[] = [];
	let pendingFolders: string[] = [];
	let loading = false;
	let uploading = false;

	let expandedSources: Record<string, boolean> = {};
	const expandedKey = `skill-files-expanded-${skillId}`;

	let tree: SkillTreeNode = buildTree([], []);
	$: tree = buildTree(fileItems, pendingFolders);

	// A pending (client-only) folder becomes real once a persisted file lives
	// under it; drop the now-redundant stub so it cannot resurrect an empty node.
	$: {
		const realPrefixes = new Set<string>();
		for (const item of fileItems) {
			const segments = item.path.split('/');
			for (let i = 1; i < segments.length; i += 1) {
				realPrefixes.add(segments.slice(0, i).join('/'));
			}
		}
		const next = pendingFolders.filter((p) => !realPrefixes.has(p));
		if (next.length !== pendingFolders.length) {
			pendingFolders = next;
		}
	}

	// ===== expand-state persistence (RecursiveFolder pattern) =====
	const loadExpanded = () => {
		try {
			const raw = localStorage.getItem(expandedKey);
			expandedSources = raw ? JSON.parse(raw) : {};
		} catch {
			expandedSources = {};
		}
	};

	$: if (expandedSources && skillId) {
		try {
			localStorage.setItem(expandedKey, JSON.stringify(expandedSources));
		} catch {
			// ignore quota / serialization failures — persistence is best-effort
		}
	}

	const joinPath = (prefix: string, name: string): string => (prefix ? `${prefix}/${name}` : name);

	const refreshFileList = async () => {
		loading = true;
		try {
			const res = await getSkillFileList(localStorage.token, skillId);
			fileItems = res?.items ?? [];
		} catch (e) {
			console.error('Failed to load skill files:', e);
		}
		loading = false;
		// Keep the right pane consistent with whatever the server now reports.
		await syncActivePane();
	};

	// ===== active file (right pane) =====
	// `activeKind`: 'text' (CodeEditor) | 'image' | 'binary' | '' (no selection)
	let activePath = '';
	let activeName = '';
	let activeKind: 'text' | 'image' | 'binary' | '' = '';
	let activeMediaType = '';
	let activeSize: number | undefined;
	let activeContent = ''; // text editor buffer
	let activeImageUrl = ''; // object URL for image preview
	let paneLoading = false;
	let saving = false;

	const revokeImageUrl = () => {
		if (activeImageUrl) {
			URL.revokeObjectURL(activeImageUrl);
			activeImageUrl = '';
		}
	};

	const itemFor = (path: string): SkillFileItem | undefined =>
		fileItems.find((f) => f.path === path);

	const mediaTypeFor = (item: SkillFileItem | undefined): string =>
		(item?.media_type as string) ?? (item?.meta?.content_type as string) ?? '';

	const sizeFor = (item: SkillFileItem | undefined): number | undefined =>
		(item?.size as number) ?? (item?.meta?.size as number) ?? undefined;

	// Re-derive the right pane after a list refresh: keep selection if the file
	// still exists, otherwise clear it.
	const syncActivePane = async () => {
		if (!activePath) return;
		const item = itemFor(activePath);
		if (!item) {
			clearActive();
			return;
		}
		// A text file's buffer follows the server only when the user has no
		// pending in-editor change against it; for simplicity we leave the
		// editor buffer alone (the user may be mid-edit) and only refresh
		// non-text panes' metadata.
		activeMediaType = mediaTypeFor(item);
		activeSize = sizeFor(item);
	};

	const clearActive = () => {
		revokeImageUrl();
		activePath = '';
		activeName = '';
		activeKind = '';
		activeMediaType = '';
		activeSize = undefined;
		activeContent = '';
	};

	const selectFile = async (file: { path: string }) => {
		const item = itemFor(file.path);
		if (!item) return;

		revokeImageUrl();
		activePath = file.path;
		activeName = file.path.split('/').pop() ?? file.path;
		activeMediaType = mediaTypeFor(item);
		activeSize = sizeFor(item);

		if (isTextPath(file.path)) {
			activeKind = 'text';
			activeContent = (item.data?.content as string) ?? '';
			return;
		}

		if (activeMediaType.startsWith('image/')) {
			activeKind = 'image';
			activeContent = '';
			paneLoading = true;
			try {
				const blob = await getSkillFileContentBlob(localStorage.token, skillId, file.path);
				// Guard against a race: a newer selection may have superseded this one.
				if (activePath === file.path) {
					activeImageUrl = URL.createObjectURL(blob);
				}
			} catch (e) {
				toast.error(`${e}`);
			}
			paneLoading = false;
			return;
		}

		activeKind = 'binary';
		activeContent = '';
	};

	const saveActiveFile = async () => {
		if (activeKind !== 'text' || !activePath) return;
		saving = true;
		try {
			await updateSkillFileContent(localStorage.token, skillId, {
				path: activePath,
				content: activeContent
			});
			toast.success($i18n.t('Saved'));
			await refreshFileList();
		} catch (e) {
			toast.error(`${e}`);
		}
		saving = false;
	};

	const triggerSaveContent = (blob: Blob, filename: string) => {
		const url = URL.createObjectURL(blob);
		const a = document.createElement('a');
		a.href = url;
		a.download = filename;
		document.body.appendChild(a);
		a.click();
		a.remove();
		URL.revokeObjectURL(url);
	};

	const downloadActiveFile = async () => {
		if (!activePath) return;
		try {
			const blob = await getSkillFileContentBlob(localStorage.token, skillId, activePath);
			triggerSaveContent(blob, activeName);
		} catch (e) {
			toast.error(`${e}`);
		}
	};

	// ===== upload (button / menu / drop / replace) =====
	const uploadFilesToFolder = async (files: File[], folderPrefix: string) => {
		if (files.length === 0) return;

		uploading = true;
		for (const file of files) {
			try {
				const uploaded = await uploadFile(localStorage.token, file, null, false);
				if (!uploaded) {
					toast.error($i18n.t('Failed to upload file.'));
					continue;
				}
				await createSkillFile(localStorage.token, skillId, {
					path: joinPath(folderPrefix, file.name),
					file_id: uploaded.id
				});
			} catch (e) {
				toast.error(`${e}`);
			}
		}
		uploading = false;
		// The pending-folder stub (if any) is pruned reactively once the refreshed
		// list shows a persisted file under it.
		await refreshFileList();
	};

	let uploadInput: HTMLInputElement;
	let uploadTargetPrefix = '';
	const triggerUpload = (folderPrefix: string) => {
		uploadTargetPrefix = folderPrefix;
		uploadInput?.click();
	};

	// Replace the active file in place (re-upload to the same path).
	let replaceInput: HTMLInputElement;
	const triggerReplace = () => {
		if (!activePath) return;
		replaceInput?.click();
	};

	const replaceActiveFile = async (file: File) => {
		if (!activePath) return;
		uploading = true;
		try {
			const uploaded = await uploadFile(localStorage.token, file, null, false);
			if (!uploaded) {
				toast.error($i18n.t('Failed to upload file.'));
			} else {
				await createSkillFile(localStorage.token, skillId, {
					path: activePath,
					file_id: uploaded.id
				});
				toast.success($i18n.t('Saved'));
			}
		} catch (e) {
			toast.error(`${e}`);
		}
		uploading = false;
		// Refresh and re-render the pane (metadata + any new image bytes).
		await refreshFileList();
		const reselect = activePath;
		clearActive();
		await selectFile({ path: reselect });
	};

	// ===== new file (name prompt -> create empty -> edit in right pane) =====
	let showNewFileModal = false;
	let newFileFolder = '';
	let newFileName = '';

	const openNewFile = (folderPrefix: string) => {
		newFileFolder = folderPrefix;
		newFileName = '';
		showNewFileModal = true;
	};

	const createNewFile = async (inputName?: string) => {
		const name = (inputName ?? newFileName).trim();
		if (!name) {
			toast.error($i18n.t('File name cannot be empty.'));
			return;
		}
		const path = joinPath(newFileFolder, name);
		try {
			await createSkillFileInline(localStorage.token, skillId, { path, content: '' });
			pendingFolders = pendingFolders.filter((p) => p !== newFileFolder);
			await refreshFileList();
			// Open the freshly created file for editing in the right pane.
			clearActive();
			await selectFile({ path });
		} catch (e) {
			toast.error(`${e}`);
		}
	};

	// ===== new folder (client-side pending node) =====
	let showNewFolderModal = false;
	let newFolderParent = '';
	let newFolderName = '';

	const openNewFolder = (folderPrefix: string) => {
		newFolderParent = folderPrefix;
		newFolderName = '';
		showNewFolderModal = true;
	};

	const createPendingFolder = async (inputName?: string) => {
		const name = (inputName ?? newFolderName).trim();
		if (!name) {
			toast.error($i18n.t('Folder name cannot be empty.'));
			return;
		}
		const prefix = joinPath(newFolderParent, name);
		if (!pendingFolders.includes(prefix)) {
			pendingFolders = [...pendingFolders, prefix];
		}
		// Expand the parent so the new folder is visible.
		await tick();
		expandedSources = { ...expandedSources, [`${expandedKey}/${prefix}`]: true };
		if (newFolderParent) {
			expandedSources[`${expandedKey}/${newFolderParent}`] = true;
		}
	};

	// ===== rename / move (file or folder) =====
	let showRenameModal = false;
	let renameIsFolder = false;
	let renameFrom = ''; // file path or folder prefix
	let renameValue = ''; // editable basename

	const openRenameFile = (file: { path: string; name: string }) => {
		renameIsFolder = false;
		renameFrom = file.path;
		renameValue = file.name;
		showRenameModal = true;
	};

	const openRenameFolder = (folderPrefix: string) => {
		renameIsFolder = true;
		renameFrom = folderPrefix;
		renameValue = folderPrefix.split('/').pop() ?? folderPrefix;
		showRenameModal = true;
	};

	const confirmRename = async (inputValue?: string) => {
		const value = (inputValue ?? renameValue).trim();
		if (!value) return;

		const parent = renameFrom.includes('/') ? renameFrom.slice(0, renameFrom.lastIndexOf('/')) : '';
		const toPath = joinPath(parent, value);
		if (toPath === renameFrom) return;

		// A pending (not-yet-persisted) folder is renamed purely client-side.
		if (renameIsFolder && pendingFolders.includes(renameFrom)) {
			pendingFolders = pendingFolders.map((p) => (p === renameFrom ? toPath : p));
			return;
		}

		try {
			await moveSkillFile(localStorage.token, skillId, { from_path: renameFrom, to_path: toPath });
			// Follow the rename in the right pane when the active file moved.
			if (!renameIsFolder && activePath === renameFrom) {
				clearActive();
				await refreshFileList();
				await selectFile({ path: toPath });
			} else {
				await refreshFileList();
			}
		} catch (e) {
			toast.error(`${e}`);
		}
	};

	// ===== delete (file or folder) =====
	let showDeleteConfirm = false;
	let deleteIsFolder = false;
	let deletePath = '';
	let deleteLabel = '';

	const openDeleteFile = (file: { path: string; name: string }) => {
		deleteIsFolder = false;
		deletePath = file.path;
		deleteLabel = file.name;
		showDeleteConfirm = true;
	};

	const openDeleteFolder = (folderPrefix: string) => {
		deleteIsFolder = true;
		deletePath = folderPrefix;
		deleteLabel = folderPrefix.split('/').pop() ?? folderPrefix;
		showDeleteConfirm = true;
	};

	const confirmDelete = async () => {
		// A pending folder has nothing on the server — drop it locally.
		if (deleteIsFolder && pendingFolders.includes(deletePath)) {
			pendingFolders = pendingFolders.filter((p) => p !== deletePath);
			return;
		}
		// If the active file is being removed, clear the right pane.
		const removingActive =
			activePath === deletePath || (deleteIsFolder && activePath.startsWith(`${deletePath}/`));
		try {
			// The backend cascade-deletes the backing File(s); no separate
			// deleteFileById call is needed (and it would 404 on a removed file).
			await removeSkillFilePath(localStorage.token, skillId, deletePath);
			if (removingActive) {
				clearActive();
			}
			await refreshFileList();
		} catch (e) {
			toast.error(`${e}`);
		}
	};

	// ===== dropzone (scoped to the tree container) =====
	let dragged = false;
	let dragOverPrefix: string | null = null;

	const folderPrefixFromEvent = (e: DragEvent): string => {
		const target = e.target as HTMLElement | null;
		const folderEl = target?.closest?.('[data-folder-prefix]') as HTMLElement | null;
		return folderEl?.dataset?.folderPrefix ?? '';
	};

	const onDragOver = (e: DragEvent) => {
		if (disabled) return;
		e.preventDefault();
		if (e.dataTransfer?.types?.includes('Files')) {
			dragged = true;
			dragOverPrefix = folderPrefixFromEvent(e) || '';
		} else {
			dragged = false;
		}
	};

	const onDragLeave = () => {
		dragged = false;
		dragOverPrefix = null;
	};

	const onDrop = async (e: DragEvent) => {
		if (disabled) return;
		e.preventDefault();
		const targetPrefix = folderPrefixFromEvent(e) || '';
		dragged = false;
		dragOverPrefix = null;

		if (e.dataTransfer?.types?.includes('Files') && e.dataTransfer.files?.length > 0) {
			await uploadFilesToFolder(Array.from(e.dataTransfer.files), targetPrefix);
		}
	};

	onMount(() => {
		loadExpanded();
		refreshFileList();
	});

	onDestroy(() => {
		revokeImageUrl();
	});
</script>

<input
	bind:this={uploadInput}
	type="file"
	multiple
	hidden
	on:change={async (e) => {
		const target = e.target as HTMLInputElement;
		if (target.files && target.files.length > 0) {
			await uploadFilesToFolder(Array.from(target.files), uploadTargetPrefix);
			target.value = '';
		}
	}}
/>

<input
	bind:this={replaceInput}
	type="file"
	hidden
	on:change={async (e) => {
		const target = e.target as HTMLInputElement;
		if (target.files && target.files.length > 0) {
			await replaceActiveFile(target.files[0]);
			target.value = '';
		}
	}}
/>

<!-- New file name prompt -->
<ConfirmDialog
	bind:show={showNewFileModal}
	title={$i18n.t('New file')}
	confirmLabel={$i18n.t('New file')}
	input={true}
	inputPlaceholder={$i18n.t('File name')}
	inputValue={newFileName}
	on:confirm={(e) => createNewFile(e.detail)}
/>

<!-- New folder name prompt -->
<ConfirmDialog
	bind:show={showNewFolderModal}
	title={$i18n.t('New folder')}
	confirmLabel={$i18n.t('New folder')}
	input={true}
	inputPlaceholder={$i18n.t('Folder name')}
	inputValue={newFolderName}
	on:confirm={(e) => createPendingFolder(e.detail)}
/>

<!-- Rename / move prompt -->
<ConfirmDialog
	bind:show={showRenameModal}
	title={$i18n.t('Rename')}
	confirmLabel={$i18n.t('Rename')}
	input={true}
	inputPlaceholder={renameIsFolder ? $i18n.t('Folder name') : $i18n.t('File name')}
	inputValue={renameValue}
	on:confirm={(e) => confirmRename(e.detail)}
/>

<!-- Delete confirm -->
<ConfirmDialog
	bind:show={showDeleteConfirm}
	title={$i18n.t('Delete')}
	confirmLabel={$i18n.t('Delete')}
	message={$i18n.t('Are you sure you want to delete "{{NAME}}"?', { NAME: deleteLabel })}
	on:confirm={confirmDelete}
/>

<div class="mt-2 flex flex-col gap-2">
	<div class="flex items-center justify-between">
		<div class="text-sm font-medium text-gray-700 dark:text-gray-300">
			{$i18n.t('Reference files')}
		</div>

		{#if !disabled}
			<div class="flex items-center gap-1">
				<button
					type="button"
					class="px-2.5 py-1 text-xs font-medium bg-gray-100 hover:bg-gray-200 dark:bg-gray-800 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300 rounded-full transition flex items-center gap-1"
					on:click={() => openNewFile('')}
				>
					<PagePlus className="size-3" />
					{$i18n.t('New file')}
				</button>
				<button
					type="button"
					class="px-2.5 py-1 text-xs font-medium bg-gray-100 hover:bg-gray-200 dark:bg-gray-800 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300 rounded-full transition flex items-center gap-1"
					on:click={() => openNewFolder('')}
				>
					<NewFolderAlt className="size-3" />
					{$i18n.t('New folder')}
				</button>
				<button
					type="button"
					class="px-2.5 py-1 text-xs font-medium bg-gray-100 hover:bg-gray-200 dark:bg-gray-800 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300 rounded-full transition flex items-center gap-1"
					on:click={() => triggerUpload('')}
					disabled={uploading}
				>
					{#if uploading}
						<Spinner className="size-3" />
					{:else}
						<ArrowUpTray className="size-3" />
					{/if}
					{$i18n.t('Add files')}
				</button>
			</div>
		{/if}
	</div>

	<!-- Vertical split: tree (left) | active-file pane (right) -->
	<div
		class="flex gap-2 rounded-xl border transition {dragged
			? 'border-gray-300 dark:border-gray-600 bg-gray-50/50 dark:bg-gray-900/50'
			: 'border-gray-100/50 dark:border-gray-850/50'} h-[28rem]"
	>
		<!-- LEFT: folder tree -->
		<div
			class="w-2/5 min-w-[12rem] overflow-y-auto p-1.5 border-e border-gray-100/50 dark:border-gray-850/50"
			role="group"
			on:dragover={onDragOver}
			on:dragleave={onDragLeave}
			on:drop={onDrop}
		>
			{#if loading}
				<div class="flex items-center justify-center py-3">
					<Spinner className="size-4" />
				</div>
			{:else if tree.children.length === 0 && tree.files.length === 0}
				<div class="px-2 py-3 text-xs text-gray-400 dark:text-gray-500 italic">
					{$i18n.t('No reference files attached.')}
				</div>
			{:else}
				<SkillFileTreeNode
					node={tree}
					{expandedKey}
					bind:expandedSources
					{disabled}
					activePath={activePath}
					onOpenFile={selectFile}
					onNewFile={openNewFile}
					onNewFolder={openNewFolder}
					onUpload={triggerUpload}
					onRenameFile={openRenameFile}
					onDeleteFile={openDeleteFile}
					onRenameFolder={openRenameFolder}
					onDeleteFolder={openDeleteFolder}
					bind:dragOverPrefix
				/>
			{/if}
		</div>

		<!-- RIGHT: active-file pane -->
		<div class="flex-1 min-w-0 flex flex-col">
			{#if activeKind === ''}
				<div class="flex-1 flex items-center justify-center text-xs text-gray-400 dark:text-gray-500 italic">
					{$i18n.t('Select a file to edit')}
				</div>
			{:else}
				<!-- Pane header -->
				<div class="flex items-center justify-between gap-2 px-2 py-1.5 border-b border-gray-100/50 dark:border-gray-850/50">
					<div class="flex items-center gap-1.5 min-w-0 text-gray-600 dark:text-gray-300">
						<DocumentPage className="size-3.5 shrink-0" />
						<span class="line-clamp-1 text-xs font-medium">{activeName}</span>
					</div>
					<div class="flex items-center gap-1 shrink-0">
						{#if activeKind === 'text' && !disabled}
							<button
								type="button"
								class="px-2.5 py-1 text-xs font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 rounded-full transition flex items-center gap-1"
								on:click={saveActiveFile}
								disabled={saving}
							>
								{#if saving}
									<Spinner className="size-3" />
								{/if}
								{$i18n.t('Save')}
							</button>
						{/if}
						<button
							type="button"
							class="px-2.5 py-1 text-xs font-medium bg-gray-100 hover:bg-gray-200 dark:bg-gray-800 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300 rounded-full transition flex items-center gap-1"
							on:click={downloadActiveFile}
						>
							<ArrowDownTray className="size-3" />
							{$i18n.t('Download')}
						</button>
						{#if activeKind !== 'text' && !disabled}
							<button
								type="button"
								class="px-2.5 py-1 text-xs font-medium bg-gray-100 hover:bg-gray-200 dark:bg-gray-800 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300 rounded-full transition flex items-center gap-1"
								on:click={triggerReplace}
								disabled={uploading}
							>
								{#if uploading}
									<Spinner className="size-3" />
								{:else}
									<ArrowUpTray className="size-3" />
								{/if}
								{$i18n.t('Replace file')}
							</button>
						{/if}
					</div>
				</div>

				<!-- Pane body -->
				<div class="flex-1 min-h-0 overflow-hidden">
					{#if activeKind === 'text'}
						{#key activePath}
							<CodeEditor
								id={`skill-file-${skillId}`}
								lang={languageForPath(activePath)}
								value={activeContent}
								onChange={(e?: string) => {
									activeContent = e ?? '';
								}}
								onSave={saveActiveFile}
							/>
						{/key}
					{:else if activeKind === 'image'}
						<div class="h-full overflow-auto flex items-center justify-center p-3 bg-gray-50/50 dark:bg-gray-900/50">
							{#if paneLoading}
								<Spinner className="size-5" />
							{:else if activeImageUrl}
								<img src={activeImageUrl} alt={activeName} class="max-w-full max-h-full object-contain" />
							{:else}
								<div class="text-xs text-gray-400 dark:text-gray-500 italic">
									{$i18n.t('Preview')}
								</div>
							{/if}
						</div>
					{:else}
						<div class="h-full overflow-auto flex flex-col items-center justify-center gap-1 p-4 text-center">
							<div class="text-xs text-gray-500 dark:text-gray-400">
								{$i18n.t('Binary file — preview not available')}
							</div>
							<div class="text-xs text-gray-400 dark:text-gray-500">
								{activeMediaType || $i18n.t('Type')}{#if activeSize}
									· {formatFileSize(activeSize)}{/if}
							</div>
						</div>
					{/if}
				</div>
			{/if}
		</div>
	</div>
</div>
