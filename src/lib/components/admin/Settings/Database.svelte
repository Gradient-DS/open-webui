<script lang="ts">
	import fileSaver from 'file-saver';
	const { saveAs } = fileSaver;

	import { downloadDatabase, exportDatabaseJson } from '$lib/apis/utils';
	import { onMount, getContext } from 'svelte';
	import { config, user } from '$lib/stores';
	import { toast } from 'svelte-sonner';
	import { getAllUserChats } from '$lib/apis/chats';
	import { getAllUsers } from '$lib/apis/users';
	import { exportConfig, importConfig } from '$lib/apis/configs';
	import {
		getArchives,
		getArchive,
		deleteArchive,
		exportArchiveChats,
		getArchiveConfig,
		updateArchiveConfig
	} from '$lib/apis/archives';

	const i18n = getContext('i18n');

	export let saveHandler: Function;

	// Archives state
	let archives: any[] = [];
	let archivesTotal = 0;
	let archivesLoading = true;
	let archiveSearch = '';

	let archiveConfig = {
		enable_user_archival: true,
		default_archive_retention_days: 1095,
		enable_auto_archive_on_self_delete: false,
		auto_archive_retention_days: 365
	};

	// Modal state
	let showArchiveModal = false;
	let selectedArchive: any = null;
	let loadingArchive = false;

	const exportAllUserChats = async () => {
		let blob = new Blob([JSON.stringify(await getAllUserChats(localStorage.token))], {
			type: 'application/json'
		});
		saveAs(blob, `all-chats-export-${Date.now()}.json`);
	};

	const exportUsers = async () => {
		const users = await getAllUsers(localStorage.token);

		const headers = ['id', 'name', 'email', 'role'];

		const csv = [
			headers.join(','),
			...users.users.map((user) => {
				return headers
					.map((header) => {
						if (user[header] === null || user[header] === undefined) {
							return '';
						}
						return `"${String(user[header]).replace(/"/g, '""')}"`;
					})
					.join(',');
			})
		].join('\n');

		const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
		saveAs(blob, 'users.csv');
	};

	async function loadArchiveConfig() {
		try {
			const result = await getArchiveConfig(localStorage.token);
			if (result) {
				archiveConfig = result;
			}
		} catch (error) {
			console.error('Failed to load archive config:', error);
		}
	}

	async function loadArchives() {
		archivesLoading = true;
		try {
			const response = await getArchives(localStorage.token, {
				search: archiveSearch || undefined
			});
			archives = response.items;
			archivesTotal = response.total;
		} catch (error) {
			// Archives might not be enabled
			console.log('Archives not available:', error);
		}
		archivesLoading = false;
	}

	async function handleViewArchive(archiveId: string) {
		loadingArchive = true;
		showArchiveModal = true;
		try {
			selectedArchive = await getArchive(localStorage.token, archiveId);
		} catch (error) {
			toast.error($i18n.t('Failed to load archive details'));
			showArchiveModal = false;
		}
		loadingArchive = false;
	}

	async function handleExportArchive(archiveId: string, userEmail: string) {
		try {
			// Export in native Open WebUI format (can be imported via Settings > Data Controls > Import Chats)
			const chats = await exportArchiveChats(localStorage.token, archiveId);
			if (chats) {
				const blob = new Blob([JSON.stringify(chats)], {
					type: 'application/json'
				});
				saveAs(blob, `chat-export-${userEmail}-${Date.now()}.json`);
				toast.success($i18n.t('Archive exported'));
			}
		} catch (error) {
			toast.error($i18n.t('Failed to export archive'));
		}
	}

	async function handleDeleteArchive(archiveId: string) {
		if (!confirm($i18n.t('Are you sure you want to permanently delete this archive? This action cannot be undone.'))) {
			return;
		}
		try {
			await deleteArchive(localStorage.token, archiveId);
			toast.success($i18n.t('Archive deleted'));
			loadArchives();
		} catch (error) {
			toast.error($i18n.t('Failed to delete archive'));
		}
	}

	async function handleSaveArchiveConfig() {
		try {
			await updateArchiveConfig(localStorage.token, archiveConfig);
			toast.success($i18n.t('Archive settings saved'));
		} catch (error) {
			toast.error($i18n.t('Failed to save settings'));
		}
	}

	function formatDate(timestamp: number) {
		return new Date(timestamp * 1000).toLocaleDateString();
	}

	onMount(async () => {
		await loadArchiveConfig();
		await loadArchives();
	});
</script>

<form
	class="flex flex-col h-full justify-between space-y-3 text-sm"
	on:submit|preventDefault={async () => {
		await handleSaveArchiveConfig();
		saveHandler();
	}}
>
	<div class=" space-y-3 overflow-y-scroll scrollbar-hidden h-full">
		<div>
			<div class=" mb-2 text-sm font-medium">{$i18n.t('Database')}</div>

			<input
				id="config-json-input"
				hidden
				type="file"
				accept=".json"
				on:change={(e) => {
					const file = e.target.files[0];
					const reader = new FileReader();

					reader.onload = async (e) => {
						const res = await importConfig(localStorage.token, JSON.parse(e.target.result)).catch(
							(error) => {
								toast.error(`${error}`);
							}
						);

						if (res) {
							toast.success($i18n.t('Config imported successfully'));
						}
						e.target.value = null;
					};

					reader.readAsText(file);
				}}
			/>

			<button
				type="button"
				class=" flex rounded-md py-2 px-3 w-full hover:bg-gray-200 dark:hover:bg-gray-800 transition"
				on:click={async () => {
					document.getElementById('config-json-input').click();
				}}
			>
				<div class=" self-center mr-3">
					<svg
						xmlns="http://www.w3.org/2000/svg"
						viewBox="0 0 16 16"
						fill="currentColor"
						class="w-4 h-4"
					>
						<path d="M2 3a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V3Z" />
						<path
							fill-rule="evenodd"
							d="M13 6H3v6a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2V6ZM8.75 7.75a.75.75 0 0 0-1.5 0v2.69L6.03 9.22a.75.75 0 0 0-1.06 1.06l2.5 2.5a.75.75 0 0 0 1.06 0l2.5-2.5a.75.75 0 1 0-1.06-1.06l-1.22 1.22V7.75Z"
							clip-rule="evenodd"
						/>
					</svg>
				</div>
				<div class=" self-center text-sm font-medium">
					{$i18n.t('Import Config from JSON File')}
				</div>
			</button>

			<button
				type="button"
				class=" flex rounded-md py-2 px-3 w-full hover:bg-gray-200 dark:hover:bg-gray-800 transition"
				on:click={async () => {
					const config = await exportConfig(localStorage.token);
					const blob = new Blob([JSON.stringify(config)], {
						type: 'application/json'
					});
					saveAs(blob, `config-${Date.now()}.json`);
				}}
			>
				<div class=" self-center mr-3">
					<svg
						xmlns="http://www.w3.org/2000/svg"
						viewBox="0 0 16 16"
						fill="currentColor"
						class="w-4 h-4"
					>
						<path d="M2 3a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V3Z" />
						<path
							fill-rule="evenodd"
							d="M13 6H3v6a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2V6ZM8.75 7.75a.75.75 0 0 0-1.5 0v2.69L6.03 9.22a.75.75 0 0 0-1.06 1.06l2.5 2.5a.75.75 0 0 0 1.06 0l2.5-2.5a.75.75 0 1 0-1.06-1.06l-1.22 1.22V7.75Z"
							clip-rule="evenodd"
						/>
					</svg>
				</div>
				<div class=" self-center text-sm font-medium">
					{$i18n.t('Export Config to JSON File')}
				</div>
			</button>

			<hr class="border-gray-50 dark:border-gray-850/30 my-1" />

			{#if $config?.features.enable_admin_export ?? true}
				{#if $config?.database?.type === 'sqlite'}
					<button
						class=" flex rounded-md py-1.5 px-3 w-full hover:bg-gray-200 dark:hover:bg-gray-800 transition"
						type="button"
						on:click={() => {
							downloadDatabase(localStorage.token).catch((error) => {
								toast.error(`${error}`);
							});
						}}
					>
						<div class=" self-center mr-3">
							<svg
								xmlns="http://www.w3.org/2000/svg"
								viewBox="0 0 16 16"
								fill="currentColor"
								class="w-4 h-4"
							>
								<path d="M2 3a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V3Z" />
								<path
									fill-rule="evenodd"
									d="M13 6H3v6a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2V6ZM8.75 7.75a.75.75 0 0 0-1.5 0v2.69L6.03 9.22a.75.75 0 0 0-1.06 1.06l2.5 2.5a.75.75 0 0 0 1.06 0l2.5-2.5a.75.75 0 1 0-1.06-1.06l-1.22 1.22V7.75Z"
									clip-rule="evenodd"
								/>
							</svg>
						</div>
						<div class=" self-center text-sm font-medium">{$i18n.t('Download Database')}</div>
					</button>
				{/if}

				<button
						class=" flex rounded-md py-1.5 px-3 w-full hover:bg-gray-200 dark:hover:bg-gray-800 transition"
						type="button"
						on:click={() => {
							exportDatabaseJson(localStorage.token).catch((error) => {
								toast.error(`${error}`);
							});
						}}
					>
						<div class=" self-center mr-3">
							<svg
								xmlns="http://www.w3.org/2000/svg"
								viewBox="0 0 16 16"
								fill="currentColor"
								class="w-4 h-4"
							>
								<path d="M2 3a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V3Z" />
								<path
									fill-rule="evenodd"
									d="M13 6H3v6a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2V6ZM8.75 7.75a.75.75 0 0 0-1.5 0v2.69L6.03 9.22a.75.75 0 0 0-1.06 1.06l2.5 2.5a.75.75 0 0 0 1.06 0l2.5-2.5a.75.75 0 1 0-1.06-1.06l-1.22 1.22V7.75Z"
									clip-rule="evenodd"
								/>
							</svg>
						</div>
						<div class=" self-center text-sm font-medium">{$i18n.t('Export Database as JSON')}</div>
				</button>

				<button
					class=" flex rounded-md py-2 px-3 w-full hover:bg-gray-200 dark:hover:bg-gray-800 transition"
					on:click={() => {
						exportAllUserChats();
					}}
				>
					<div class=" self-center mr-3">
						<svg
							xmlns="http://www.w3.org/2000/svg"
							viewBox="0 0 16 16"
							fill="currentColor"
							class="w-4 h-4"
						>
							<path d="M2 3a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V3Z" />
							<path
								fill-rule="evenodd"
								d="M13 6H3v6a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2V6ZM8.75 7.75a.75.75 0 0 0-1.5 0v2.69L6.03 9.22a.75.75 0 0 0-1.06 1.06l2.5 2.5a.75.75 0 0 0 1.06 0l2.5-2.5a.75.75 0 1 0-1.06-1.06l-1.22 1.22V7.75Z"
								clip-rule="evenodd"
							/>
						</svg>
					</div>
					<div class=" self-center text-sm font-medium">
						{$i18n.t('Export All Chats (All Users)')}
					</div>
				</button>

				<button
					class=" flex rounded-md py-2 px-3 w-full hover:bg-gray-200 dark:hover:bg-gray-800 transition"
					on:click={() => {
						exportUsers();
					}}
				>
					<div class=" self-center mr-3">
						<svg
							xmlns="http://www.w3.org/2000/svg"
							viewBox="0 0 16 16"
							fill="currentColor"
							class="w-4 h-4"
						>
							<path d="M2 3a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V3Z" />
							<path
								fill-rule="evenodd"
								d="M13 6H3v6a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2V6ZM8.75 7.75a.75.75 0 0 0-1.5 0v2.69L6.03 9.22a.75.75 0 0 0-1.06 1.06l2.5 2.5a.75.75 0 0 0 1.06 0l2.5-2.5a.75.75 0 1 0-1.06-1.06l-1.22 1.22V7.75Z"
								clip-rule="evenodd"
							/>
						</svg>
					</div>
					<div class=" self-center text-sm font-medium">
						{$i18n.t('Export Users')}
					</div>
				</button>
			{/if}
		</div>

		<!-- User Archives Section -->
		{#if archiveConfig.enable_user_archival}
			<hr class="border-gray-50 dark:border-gray-850/30 my-2" />

			<div>
				<div class="flex items-center justify-between mb-2">
					<div class="text-sm font-medium">{$i18n.t('User Archives')}</div>
					<span class="text-xs text-gray-500">{archivesTotal} {$i18n.t('archives')}</span>
				</div>

				<!-- Archive Settings -->
				<div class="mb-3 space-y-2">
					<div class="flex w-full justify-between items-center">
						<div class="self-center text-xs">{$i18n.t('Default Retention (days)')}</div>
						<input
							type="number"
							class="w-20 rounded py-1 px-2 text-xs bg-gray-50 dark:bg-gray-850 dark:text-gray-300"
							bind:value={archiveConfig.default_archive_retention_days}
							min="1"
						/>
					</div>

					<div class="flex w-full justify-between items-center">
						<div class="self-center text-xs">{$i18n.t('Auto-archive on self-delete')}</div>
						<button
							type="button"
							class="relative inline-flex h-5 w-9 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none"
							class:bg-gray-200={!archiveConfig.enable_auto_archive_on_self_delete}
							class:dark:bg-gray-700={!archiveConfig.enable_auto_archive_on_self_delete}
							class:bg-emerald-500={archiveConfig.enable_auto_archive_on_self_delete}
							on:click={() => {
								archiveConfig.enable_auto_archive_on_self_delete = !archiveConfig.enable_auto_archive_on_self_delete;
							}}
						>
							<span
								class="pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out"
								class:translate-x-4={archiveConfig.enable_auto_archive_on_self_delete}
								class:translate-x-0={!archiveConfig.enable_auto_archive_on_self_delete}
							/>
						</button>
					</div>
				</div>

				<!-- Search and Filter -->
				<div class="flex gap-2 mb-2">
					<input
						type="text"
						placeholder={$i18n.t('Search archives...')}
						class="flex-1 rounded py-1.5 px-2 text-xs bg-gray-50 dark:text-gray-300 dark:bg-gray-850"
						bind:value={archiveSearch}
						on:input={() => loadArchives()}
					/>
				</div>

				<!-- Archives List -->
				{#if archivesLoading}
					<div class="text-center py-2 text-gray-500 text-xs">{$i18n.t('Loading...')}</div>
				{:else if archives.length === 0}
					<div class="text-center py-2 text-gray-500 text-xs">{$i18n.t('No archives')}</div>
				{:else}
					<div class="space-y-1 max-h-48 overflow-y-auto">
						{#each archives as archive}
							<div class="p-2 border rounded dark:border-gray-800 flex items-center justify-between text-xs">
								<div class="flex-1 min-w-0">
									<div class="font-medium truncate">{archive.user_name}</div>
									<div class="text-gray-500 truncate">{archive.user_email}</div>
									<div class="text-gray-400">
										{formatDate(archive.created_at)}
										{#if archive.never_delete}
											<span class="text-blue-600 ml-1">{$i18n.t('Permanent')}</span>
										{/if}
									</div>
								</div>
								<div class="flex gap-1 ml-2">
									<button
										type="button"
										class="px-1.5 py-0.5 border rounded hover:bg-gray-100 dark:hover:bg-gray-800 dark:border-gray-700"
										on:click={() => handleViewArchive(archive.id)}
									>
										{$i18n.t('View')}
									</button>
									<button
										type="button"
										class="px-1.5 py-0.5 border rounded hover:bg-gray-100 dark:hover:bg-gray-800 dark:border-gray-700"
										on:click={() => handleExportArchive(archive.id, archive.user_email)}
									>
										{$i18n.t('Export')}
									</button>
									<button
										type="button"
										class="px-1.5 py-0.5 border rounded hover:bg-gray-100 dark:hover:bg-gray-800 dark:border-gray-700"
										on:click={() => handleDeleteArchive(archive.id)}
									>
										{$i18n.t('Delete')}
									</button>
								</div>
							</div>
						{/each}
					</div>
				{/if}
			</div>
		{/if}
	</div>

	<div class="flex justify-end pt-3 text-sm font-medium">
		<button
			class="px-4 py-2 bg-emerald-700 hover:bg-emerald-800 text-gray-100 rounded-lg"
			type="submit"
		>
			{$i18n.t('Save')}
		</button>
	</div>
</form>

<!-- Archive Details Modal -->
{#if showArchiveModal}
	<div class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
		<div class="bg-white dark:bg-gray-900 rounded-lg p-4 max-w-lg w-full mx-4 max-h-[70vh] overflow-y-auto">
			<div class="flex justify-between items-center mb-3">
				<h3 class="text-sm font-medium">{$i18n.t('Archive Details')}</h3>
				<button
					type="button"
					class="text-gray-500 hover:text-gray-700"
					on:click={() => {
						showArchiveModal = false;
						selectedArchive = null;
					}}
				>
					<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
					</svg>
				</button>
			</div>

			{#if loadingArchive}
				<div class="text-center py-4 text-sm">{$i18n.t('Loading...')}</div>
			{:else if selectedArchive}
				<div class="space-y-3 text-xs">
					<div class="grid grid-cols-2 gap-3">
						<div>
							<div class="text-gray-500">{$i18n.t('User')}</div>
							<div class="font-medium">{selectedArchive.user_name}</div>
							<div class="text-gray-500">{selectedArchive.user_email}</div>
						</div>
						<div>
							<div class="text-gray-500">{$i18n.t('Reason')}</div>
							<div>{selectedArchive.reason}</div>
						</div>
					</div>

					{#if selectedArchive.data?.stats}
						<div class="border-t dark:border-gray-800 pt-3">
							<div class="font-medium mb-2">{$i18n.t('Archived Data')}</div>
							<div class="bg-gray-100 dark:bg-gray-800 p-2 rounded text-center">
								<div class="text-lg font-bold">{selectedArchive.data.stats.chat_count}</div>
								<div class="text-gray-500">{$i18n.t('Chats')}</div>
							</div>
							<p class="text-gray-400 mt-2 text-center">
								{$i18n.t('Export to import into another user account')}
							</p>
						</div>
					{/if}
				</div>
			{/if}
		</div>
	</div>
{/if}

