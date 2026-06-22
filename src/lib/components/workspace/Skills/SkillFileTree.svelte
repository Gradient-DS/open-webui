<script lang="ts">
	import { getContext, onMount, tick } from 'svelte';
	import { toast } from 'svelte-sonner';

	import { uploadFile } from '$lib/apis/files';
	import {
		getSkillFileList,
		createSkillFile,
		createSkillFileInline,
		updateSkillFileContent,
		moveSkillFile,
		removeSkillFilePath
	} from '$lib/apis/skills';

	import {
		buildTree,
		isMarkdownPath,
		filterMarkdownFiles,
		type SkillFileItem,
		type SkillTreeNode
	} from './utils';

	import Modal from '$lib/components/common/Modal.svelte';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import CodeEditor from '$lib/components/common/CodeEditor.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import SkillFileTreeNode from './SkillFileTreeNode.svelte';
	import PagePlus from '$lib/components/icons/PagePlus.svelte';
	import NewFolderAlt from '$lib/components/icons/NewFolderAlt.svelte';
	import ArrowUpTray from '$lib/components/icons/ArrowUpTray.svelte';

	export let skillId: string;
	export let disabled = false;

	const i18n = getContext('i18n');

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
	};

	// ===== upload (button / menu / drop) =====
	const uploadFilesToFolder = async (files: File[], folderPrefix: string) => {
		const { kept, skipped } = filterMarkdownFiles(files);
		if (skipped > 0) {
			toast.error(
				$i18n.t('{{count}} files skipped — only markdown is supported.', { count: skipped })
			);
		}
		if (kept.length === 0) return;

		uploading = true;
		for (const file of kept) {
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

	// ===== new file (name + CodeEditor in one modal) =====
	let showNewFileModal = false;
	let newFileFolder = '';
	let newFileName = '';
	let newFileContent = '';

	const openNewFile = (folderPrefix: string) => {
		newFileFolder = folderPrefix;
		newFileName = '';
		newFileContent = '';
		showNewFileModal = true;
	};

	const saveNewFile = async () => {
		const name = newFileName.trim();
		// An empty name fails the markdown check, surfacing the same clear message.
		if (!isMarkdownPath(name)) {
			toast.error($i18n.t('Only markdown files are supported.'));
			return;
		}
		if (!newFileContent) {
			toast.error($i18n.t('Edit content must not be empty.'));
			return;
		}
		try {
			await createSkillFileInline(localStorage.token, skillId, {
				path: joinPath(newFileFolder, name),
				content: newFileContent
			});
			showNewFileModal = false;
			pendingFolders = pendingFolders.filter((p) => p !== newFileFolder);
			await refreshFileList();
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

	// ===== inline edit (open existing file in CodeEditor) =====
	let showEditModal = false;
	let editPath = '';
	let editName = '';
	let editContent = '';

	const openFile = async (file: { path: string }) => {
		const item = fileItems.find((f) => f.path === file.path);
		editPath = file.path;
		editName = file.path.split('/').pop() ?? file.path;
		editContent = item?.data?.content ?? '';
		showEditModal = true;
	};

	const saveEditedFile = async () => {
		if (!editContent) {
			toast.error($i18n.t('Edit content must not be empty.'));
			return;
		}
		try {
			await updateSkillFileContent(localStorage.token, skillId, {
				path: editPath,
				content: editContent
			});
			showEditModal = false;
			await refreshFileList();
		} catch (e) {
			toast.error(`${e}`);
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

		if (!renameIsFolder && !isMarkdownPath(value)) {
			toast.error($i18n.t('Only markdown files are supported.'));
			return;
		}

		// A pending (not-yet-persisted) folder is renamed purely client-side.
		if (renameIsFolder && pendingFolders.includes(renameFrom)) {
			pendingFolders = pendingFolders.map((p) => (p === renameFrom ? toPath : p));
			return;
		}

		try {
			await moveSkillFile(localStorage.token, skillId, { from_path: renameFrom, to_path: toPath });
			await refreshFileList();
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
		try {
			// The backend cascade-deletes the backing File(s); no separate
			// deleteFileById call is needed (and it would 404 on a removed file).
			await removeSkillFilePath(localStorage.token, skillId, deletePath);
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
</script>

<input
	bind:this={uploadInput}
	type="file"
	accept=".md,.markdown"
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

<!-- New file modal: name + content -->
<Modal bind:show={showNewFileModal} size="lg">
	<div class="px-5 py-4 flex flex-col h-[70vh]">
		<div class="text-lg font-medium dark:text-gray-200 mb-2">{$i18n.t('New file')}</div>
		<input
			class="w-full mb-2 rounded-lg px-3 py-2 text-sm bg-gray-50 dark:bg-gray-900 outline-hidden"
			type="text"
			placeholder={$i18n.t('File name (.md)')}
			aria-label={$i18n.t('File name (.md)')}
			bind:value={newFileName}
		/>
		<div
			class="flex-1 min-h-0 overflow-hidden rounded-lg border border-gray-100 dark:border-gray-850"
		>
			{#if showNewFileModal}
				<CodeEditor
					id={`skill-new-file-${skillId}`}
					lang="markdown"
					value={newFileContent}
					onChange={(e?: string) => {
						newFileContent = e ?? '';
					}}
					onSave={saveNewFile}
				/>
			{/if}
		</div>
		<div class="mt-3 flex justify-end gap-2">
			<button
				class="px-3.5 py-1.5 text-sm font-medium bg-gray-100 hover:bg-gray-200 dark:bg-gray-850 dark:hover:bg-gray-800 rounded-full transition"
				type="button"
				on:click={() => (showNewFileModal = false)}>{$i18n.t('Cancel')}</button
			>
			<button
				class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 rounded-full transition"
				type="button"
				on:click={saveNewFile}>{$i18n.t('Save file')}</button
			>
		</div>
	</div>
</Modal>

<!-- Inline edit modal -->
<Modal bind:show={showEditModal} size="lg">
	<div class="px-5 py-4 flex flex-col h-[70vh]">
		<div class="text-lg font-medium dark:text-gray-200 mb-2 line-clamp-1">{editName}</div>
		<div
			class="flex-1 min-h-0 overflow-hidden rounded-lg border border-gray-100 dark:border-gray-850"
		>
			{#if showEditModal}
				<CodeEditor
					id={`skill-edit-file-${skillId}`}
					lang="markdown"
					value={editContent}
					onChange={(e?: string) => {
						editContent = e ?? '';
					}}
					onSave={saveEditedFile}
				/>
			{/if}
		</div>
		<div class="mt-3 flex justify-end gap-2">
			<button
				class="px-3.5 py-1.5 text-sm font-medium bg-gray-100 hover:bg-gray-200 dark:bg-gray-850 dark:hover:bg-gray-800 rounded-full transition"
				type="button"
				on:click={() => (showEditModal = false)}>{$i18n.t('Cancel')}</button
			>
			<button
				class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 rounded-full transition"
				type="button"
				on:click={saveEditedFile}>{$i18n.t('Save file')}</button
			>
		</div>
	</div>
</Modal>

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
	inputPlaceholder={renameIsFolder ? $i18n.t('Folder name') : $i18n.t('File name (.md)')}
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

	<div
		class="rounded-xl border transition {dragged
			? 'border-gray-300 dark:border-gray-600 bg-gray-50/50 dark:bg-gray-900/50'
			: 'border-gray-100/50 dark:border-gray-850/50'} p-1.5 min-h-[3rem]"
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
				onOpenFile={openFile}
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
</div>
