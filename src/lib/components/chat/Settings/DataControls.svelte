<script lang="ts">
	import fileSaver from 'file-saver';
	const { saveAs } = fileSaver;

<<<<<<< HEAD
	import {
		chatId,
		chats,
		config,
		socket,
		user,
		settings,
		scrollPaginationEnabled,
		currentChatPage,
		pinnedChats
	} from '$lib/stores';

	import {
		archiveAllChats,
		deleteAllChats,
		getAllChats,
		getChatList,
		getPinnedChatList,
		importChats
	} from '$lib/apis/chats';
	import { triggerDataExport, getExportStatus, deleteExport } from '$lib/apis/export';
	import { getImportOrigin, convertOpenAIChats } from '$lib/utils';
	import { WEBUI_BASE_URL } from '$lib/constants';
	import { onMount, onDestroy, getContext } from 'svelte';
=======
	import { user } from '$lib/stores';
	import { refreshChatList } from '$lib/stores/chatList';

	import { archiveAllChats, deleteAllChats, getAllChats, importChats } from '$lib/apis/chats';
	import { getImportOrigin, convertOpenAIChats } from '$lib/utils';
	import { getContext } from 'svelte';
>>>>>>> upstream/main
	import { goto } from '$app/navigation';
	import { toast } from 'svelte-sonner';
	import SharedChatsModal from '$lib/components/layout/SharedChatsModal.svelte';
	import FilesModal from '$lib/components/layout/FilesModal.svelte';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import UserSettingRow from './UserSettingRow.svelte';
	import UserSettingSection from './UserSettingSection.svelte';

	const i18n = getContext('i18n');

	export let saveSettings: Function;

	// Chats
	let importFiles;

	// Data Export
	let exportStatus: 'none' | 'processing' | 'ready' = 'none';
	let exportPath: string | null = null;
	let exportRequesting = false;

	let showArchiveConfirmDialog = false;
	let showDeleteConfirmDialog = false;
	let showSharedChatsModal = false;
	let showFilesModal = false;

	let chatImportInputElement: HTMLInputElement;
	const actionButtonClass =
		'text-xs text-gray-500 transition-colors hover:text-gray-900 dark:text-gray-500 dark:hover:text-white';

	$: if (importFiles) {
		console.log(importFiles);

		let reader = new FileReader();
		reader.onload = (event) => {
			let chats = JSON.parse(event.target.result);
			console.log(chats);
			if (getImportOrigin(chats) == 'openai') {
				try {
					chats = convertOpenAIChats(chats);
				} catch (error) {
					console.log('Unable to import chats:', error);
				}
			}
			importChatsHandler(chats);
		};

		if (importFiles.length > 0) {
			reader.readAsText(importFiles[0]);
		}
	}

	const importChatsHandler = async (_chats) => {
		const res = await importChats(
			localStorage.token,
			_chats.map((chat) => {
				if (chat.chat) {
					return {
						chat: chat.chat,
						meta: chat.meta ?? {},
						pinned: false,
						folder_id: chat?.folder_id ?? null,
						created_at: chat?.created_at ?? null,
						updated_at: chat?.updated_at ?? null
					};
				} else {
					// Legacy format
					return {
						chat: chat,
						meta: {},
						pinned: false,
						folder_id: null,
						created_at: chat?.created_at ?? null,
						updated_at: chat?.updated_at ?? null
					};
				}
			})
		);
		if (res) {
			toast.success(`Successfully imported ${res.length} chats.`);
		}

		await refreshChatList(localStorage.token, { refreshPinned: true });
	};

	const exportChats = async () => {
		let blob = new Blob([JSON.stringify(await getAllChats(localStorage.token))], {
			type: 'application/json'
		});
		saveAs(blob, `chat-export-${Date.now()}.json`);
	};

	const archiveAllChatsHandler = async () => {
		await goto('/');
		await archiveAllChats(localStorage.token).catch((error) => {
			toast.error(`${error}`);
		});

		await refreshChatList(localStorage.token, { clearPinned: true });
	};

	const deleteAllChatsHandler = async () => {
		await goto('/');
		await deleteAllChats(localStorage.token).catch((error) => {
			toast.error(`${error}`);
		});

		await refreshChatList(localStorage.token);
	};

	// Data Export
	const handleExportStatus = (data: any) => {
		if (data.status === 'completed') {
			exportStatus = 'ready';
			exportPath = data.export_path;
			exportRequesting = false;
			toast.success($i18n.t('Your data export is ready for download.'));
		} else if (data.status === 'failed') {
			exportStatus = 'none';
			exportRequesting = false;
			toast.error($i18n.t('Data export failed: {{error}}', { error: data.error }));
		} else if (data.status === 'processing') {
			exportStatus = 'processing';
		}
	};

	const requestDataExport = async () => {
		exportRequesting = true;
		try {
			const res = await triggerDataExport(localStorage.token);
			if (res.status === 'ready') {
				exportStatus = 'ready';
				exportPath = res.export_path;
				exportRequesting = false;
			} else {
				exportStatus = 'processing';
				toast.success($i18n.t('Data export started. You will be notified when it is ready.'));
			}
		} catch (e) {
			exportRequesting = false;
			toast.error($i18n.t('Failed to start data export.'));
		}
	};

	const downloadDataExport = () => {
		if (exportPath) {
			const a = document.createElement('a');
			a.href = `${WEBUI_BASE_URL}/cache/${exportPath}`;
			a.download = `my-data-export.zip`;
			a.click();
		}
	};

	const deleteDataExport = async () => {
		try {
			await deleteExport(localStorage.token);
			exportStatus = 'none';
			exportPath = null;
		} catch (e) {
			toast.error($i18n.t('Failed to delete export.'));
		}
	};

	onMount(async () => {
		if ($config?.features?.enable_data_export) {
			try {
				const status = await getExportStatus(localStorage.token);
				exportStatus = status.status;
				exportPath = status.export_path || null;
			} catch (e) {
				console.error('Failed to check export status:', e);
			}
		}

		$socket?.on('export:status', handleExportStatus);
	});

	onDestroy(() => {
		$socket?.off('export:status', handleExportStatus);
	});
</script>

<SharedChatsModal bind:show={showSharedChatsModal} />
<FilesModal bind:show={showFilesModal} />

<ConfirmDialog
	title={$i18n.t('Archive All Chats')}
	message={$i18n.t('Are you sure you want to archive all chats? This action cannot be undone.')}
	bind:show={showArchiveConfirmDialog}
	on:confirm={archiveAllChatsHandler}
	on:cancel={() => {
		showArchiveConfirmDialog = false;
	}}
/>

<ConfirmDialog
	title={$i18n.t('Delete All Chats')}
	message={$i18n.t('Are you sure you want to delete all chats? This action cannot be undone.')}
	bind:show={showDeleteConfirmDialog}
	on:confirm={deleteAllChatsHandler}
	on:cancel={() => {
		showDeleteConfirmDialog = false;
	}}
/>

<div id="tab-chats" class="flex flex-col h-full text-sm">
	<h2 class="text-sm font-medium text-gray-900 dark:text-white mb-4">
		{$i18n.t('Data Controls')}
	</h2>

	<div class="flex-1 min-h-0 overflow-y-auto scrollbar-hover pr-1.5">
		<input
			id="chat-import-input"
			bind:this={chatImportInputElement}
			bind:files={importFiles}
			type="file"
			accept=".json"
			hidden
		/>

		<UserSettingSection title={$i18n.t('Chats')} first>
			{#if $user?.role === 'admin' || ($user.permissions?.chat?.import ?? true)}
				<UserSettingRow
					label={$i18n.t('Import Chats')}
					description={$i18n.t('Import chat history from a JSON export file.')}
				>
					<button
						class={actionButtonClass}
						on:click={() => {
							chatImportInputElement.click();
						}}
						type="button"
					>
						{$i18n.t('Import')}
					</button>
				</UserSettingRow>
			{/if}

			{#if $user?.role === 'admin' || ($user.permissions?.chat?.export ?? true)}
				<UserSettingRow
					label={$i18n.t('Export Chats')}
					description={$i18n.t('Download your chat history as a JSON export.')}
				>
					<button
						class={actionButtonClass}
						on:click={() => {
							exportChats();
						}}
						type="button"
					>
						{$i18n.t('Export')}
					</button>
				</UserSettingRow>
			{/if}

			<UserSettingRow
				label={$i18n.t('Shared Chats')}
				description={$i18n.t('Review and manage chats you have shared.')}
			>
				<button
					class={actionButtonClass}
					on:click={() => {
						showSharedChatsModal = true;
					}}
					type="button"
				>
					{$i18n.t('Manage')}
				</button>
			</UserSettingRow>

			<UserSettingRow
				label={$i18n.t('Archive All Chats')}
				description={$i18n.t('Move every chat into the archive after confirmation.')}
			>
				<button
					class={actionButtonClass}
					on:click={() => {
						showArchiveConfirmDialog = true;
					}}
					type="button"
				>
					{$i18n.t('Archive All')}
				</button>
			</UserSettingRow>

			{#if $user?.role === 'admin' || ($user?.permissions?.chat?.delete ?? true)}
				<UserSettingRow
					label={$i18n.t('Delete All Chats')}
					description={$i18n.t('Permanently delete every chat after confirmation.')}
				>
					<button
						class={actionButtonClass}
						on:click={() => {
							showDeleteConfirmDialog = true;
						}}
						type="button"
					>
						{$i18n.t('Delete All')}
					</button>
				</UserSettingRow>
			{/if}
		</UserSettingSection>

<<<<<<< HEAD
		<div>
			<div class="mb-1 text-sm font-medium">{$i18n.t('Files')}</div>

			<div>
				<div class="py-0.5 flex w-full justify-between">
					<div class="self-center text-xs">{$i18n.t('Manage Files')}</div>
					<button
						class="p-1 px-3 text-xs flex rounded-sm transition"
						on:click={() => {
							showFilesModal = true;
						}}
						type="button"
					>
						<span class="self-center">{$i18n.t('Manage')}</span>
					</button>
				</div>
			</div>
		</div>

		{#if $config?.features?.enable_data_export}
			<div>
				<div class="mb-1 text-sm font-medium">{$i18n.t('Data Export')}</div>
				<div class="text-xs text-gray-500 dark:text-gray-400 mb-2">
					{$i18n.t(
						'Download all your data including chats, notes, memories, prompts, tools, models, and locally uploaded files.'
					)}
				</div>

				<div>
					{#if exportStatus === 'none'}
						<div class="py-0.5 flex w-full justify-between">
							<div class="self-center text-xs">{$i18n.t('Download My Data')}</div>
							<button
								class="p-1 px-3 text-xs flex rounded-sm transition"
								on:click={requestDataExport}
								disabled={exportRequesting}
								type="button"
							>
								<span class="self-center">
									{#if exportRequesting}
										{$i18n.t('Starting...')}
									{:else}
										{$i18n.t('Export')}
									{/if}
								</span>
							</button>
						</div>
					{:else if exportStatus === 'processing'}
						<div class="py-0.5 flex w-full justify-between">
							<div class="self-center text-xs">{$i18n.t('Export in progress...')}</div>
							<div class="p-1 px-3 text-xs flex">
								<svg class="animate-spin h-4 w-4" viewBox="0 0 24 24">
									<circle
										class="opacity-25"
										cx="12"
										cy="12"
										r="10"
										stroke="currentColor"
										stroke-width="4"
										fill="none"
									/>
									<path
										class="opacity-75"
										fill="currentColor"
										d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
									/>
								</svg>
							</div>
						</div>
					{:else if exportStatus === 'ready'}
						<div class="py-0.5 flex w-full justify-between">
							<div class="self-center text-xs">{$i18n.t('Export ready')}</div>
							<div class="flex gap-1">
								<button
									class="p-1 px-3 text-xs flex rounded-sm transition"
									on:click={downloadDataExport}
									type="button"
								>
									<span class="self-center">{$i18n.t('Download')}</span>
								</button>
								<button
									class="p-1 px-3 text-xs flex rounded-sm transition text-red-500"
									on:click={deleteDataExport}
									type="button"
								>
									<span class="self-center">{$i18n.t('Delete')}</span>
								</button>
							</div>
						</div>
					{/if}
				</div>
			</div>
		{/if}
=======
		<UserSettingSection title={$i18n.t('Files')}>
			<UserSettingRow
				label={$i18n.t('Manage Files')}
				description={$i18n.t('Open the file manager for uploaded files.')}
			>
				<button
					class={actionButtonClass}
					on:click={() => {
						showFilesModal = true;
					}}
					type="button"
				>
					{$i18n.t('Manage')}
				</button>
			</UserSettingRow>
		</UserSettingSection>
>>>>>>> upstream/main
	</div>
</div>
