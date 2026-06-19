<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';

	import { uploadFile } from '$lib/apis/files';
	import { deleteFileById } from '$lib/apis/files';
	import { addFileToSkillById, removeFileFromSkillById, getSkillFileList } from '$lib/apis/skills';

	import Spinner from '$lib/components/common/Spinner.svelte';

	export let skillId: string;
	export let disabled: boolean = false;

	const i18n = getContext('i18n');

	let fileItems: any[] = [];
	let inputFiles: FileList | null = null;
	let uploading = false;

	const isMarkdownFile = (file: File): boolean => {
		const name = file.name.toLowerCase();
		return name.endsWith('.md') || name.endsWith('.markdown');
	};

	const refreshFileList = async () => {
		try {
			const res = await getSkillFileList(localStorage.token, skillId);
			if (res) {
				fileItems = res.items ?? [];
			}
		} catch (e) {
			console.error('Failed to load skill files:', e);
		}
	};

	const uploadFileHandler = async (file: File) => {
		if (!isMarkdownFile(file)) {
			toast.error($i18n.t('Only markdown files are supported.'));
			return;
		}

		try {
			const uploadedFile = await uploadFile(localStorage.token, file, null, false);
			if (!uploadedFile) {
				toast.error($i18n.t('Failed to upload file.'));
				return;
			}

			await addFileToSkillById(localStorage.token, skillId, uploadedFile.id);
			await refreshFileList();
		} catch (e) {
			toast.error(`${e}`);
		}
	};

	const uploadFilesHandler = async (files: File[]) => {
		if (files.length === 0) return;
		uploading = true;
		for (const file of files) {
			await uploadFileHandler(file);
		}
		uploading = false;
	};

	const deleteFileHandler = async (fileId: string) => {
		try {
			await removeFileFromSkillById(localStorage.token, skillId, fileId);
			await deleteFileById(localStorage.token, fileId);
			await refreshFileList();
			toast.success($i18n.t('File removed successfully.'));
		} catch (e) {
			toast.error(`${e}`);
		}
	};

	onMount(() => {
		refreshFileList();
	});
</script>

<div class="mt-2 flex flex-col gap-2">
	<div class="flex items-center justify-between">
		<div class="text-sm font-medium text-gray-700 dark:text-gray-300">
			{$i18n.t('Reference files')}
		</div>

		{#if !disabled}
			<button
				type="button"
				class="px-2.5 py-1 text-xs font-medium bg-gray-100 hover:bg-gray-200 dark:bg-gray-800 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300 rounded-full transition flex items-center gap-1"
				on:click={() => {
					const el = document.getElementById(`skill-files-input-${skillId}`);
					if (el) el.click();
				}}
				disabled={uploading}
			>
				{#if uploading}
					<Spinner className="size-3" />
				{/if}
				{$i18n.t('Add files')}
			</button>
		{/if}
	</div>

	<input
		id={`skill-files-input-${skillId}`}
		bind:files={inputFiles}
		type="file"
		accept=".md,.markdown"
		multiple
		hidden
		on:change={async () => {
			if (inputFiles && inputFiles.length > 0) {
				await uploadFilesHandler(Array.from(inputFiles));
				inputFiles = null;
				const el = document.getElementById(`skill-files-input-${skillId}`);
				if (el) (el as HTMLInputElement).value = '';
			}
		}}
	/>

	{#if fileItems.length > 0}
		<div class="flex flex-col gap-1">
			{#each fileItems as file (file.id)}
				<div
					class="flex items-center justify-between px-3 py-2 rounded-lg bg-gray-50 dark:bg-gray-900 border border-gray-100/50 dark:border-gray-850/50 text-xs"
				>
					<span class="truncate text-gray-700 dark:text-gray-300 flex-1 mr-2"
						>{file.filename ?? file.meta?.name ?? file.id}</span
					>

					{#if !disabled}
						<button
							type="button"
							class="shrink-0 text-gray-400 hover:text-red-500 dark:hover:text-red-400 transition"
							aria-label={$i18n.t('Remove')}
							on:click={() => deleteFileHandler(file.id)}
						>
							<svg
								xmlns="http://www.w3.org/2000/svg"
								viewBox="0 0 20 20"
								fill="currentColor"
								class="size-4"
							>
								<path
									fill-rule="evenodd"
									d="M8.75 1A2.75 2.75 0 0 0 6 3.75v.443c-.795.077-1.584.176-2.365.298a.75.75 0 1 0 .23 1.482l.149-.022.841 10.518A2.75 2.75 0 0 0 7.596 19h4.807a2.75 2.75 0 0 0 2.742-2.53l.841-10.52.149.023a.75.75 0 0 0 .23-1.482A41.03 41.03 0 0 0 14 3.193V3.75A2.75 2.75 0 0 0 11.25 1h-2.5ZM10 4c.84 0 1.673.025 2.5.075V3.75c0-.69-.56-1.25-1.25-1.25h-2.5c-.69 0-1.25.56-1.25 1.25v.325C8.327 4.025 9.16 4 10 4ZM8.58 7.72a.75.75 0 0 0-1.5.06l.3 7.5a.75.75 0 1 0 1.5-.06l-.3-7.5Zm4.34.06a.75.75 0 1 0-1.5-.06l-.3 7.5a.75.75 0 1 0 1.5.06l.3-7.5Z"
									clip-rule="evenodd"
								/>
							</svg>
						</button>
					{/if}
				</div>
			{/each}
		</div>
	{:else}
		<div class="text-xs text-gray-400 dark:text-gray-500 italic">
			{$i18n.t('No reference files attached.')}
		</div>
	{/if}
</div>
