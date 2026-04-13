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
	import { getDataRetentionConfig, setDataRetentionConfig } from '$lib/apis/configs';

	const i18n = getContext('i18n');

	export let saveHandler: Function;

	// Archives state
	let archives: any[] = [];
	let archivesTotal = 0;
	let archivesLoading = true;
	let archiveSearch = '';

	let retentionConfig = {
		DATA_RETENTION_TTL_DAYS: 0,
		USER_INACTIVITY_TTL_DAYS: 0,
		CHAT_RETENTION_TTL_DAYS: 0,
		KNOWLEDGE_RETENTION_TTL_DAYS: 0,
		DATA_RETENTION_WARNING_DAYS: 30,
		ENABLE_RETENTION_WARNING_EMAIL: false
	};

	function effectiveDays(override: number, master: number): number {
		return override > 0 ? override : master;
	}

	let archiveConfig = {
		enable_user_archival: true,
		default_archive_retention_days: 1095,
		enable_auto_archive_on_self_delete: false,
		auto_archive_retention_days: 365
	};

	// Retention confirm modal
	let showRetentionConfirm = false;

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

	const loadRetentionConfig = async () => {
		try {
			const res = await getDataRetentionConfig(localStorage.token);
			if (res) {
				retentionConfig = res;
			}
		} catch (err) {
			console.error('Failed to load retention config:', err);
		}
	};

	const handleSaveRetentionConfig = async () => {
		try {
			await setDataRetentionConfig(localStorage.token, retentionConfig);
			toast.success($i18n.t('Data retention settings saved'));
		} catch (err) {
			toast.error($i18n.t('Failed to save data retention settings'));
		}
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
		if (
			!confirm(
				$i18n.t(
					'Are you sure you want to permanently delete this archive? This action cannot be undone.'
				)
			)
		) {
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
		await loadRetentionConfig();
		await loadArchiveConfig();
		await loadArchives();
	});
</script>

<div class="flex flex-col h-full justify-between text-sm">
	<div class="space-y-3 overflow-y-scroll scrollbar-hidden h-full">
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

		<div>
			<div class="mb-1 text-sm font-medium">{$i18n.t('Config')}</div>

			<div>
				<div class="py-0.5 flex w-full justify-between">
					<div class="self-center text-xs">{$i18n.t('Import Config')}</div>
					<button
						class="p-1 px-3 text-xs flex rounded-sm transition"
						on:click={() => {
							document.getElementById('config-json-input').click();
						}}
						type="button"
					>
						<span class="self-center">{$i18n.t('Import')}</span>
					</button>
				</div>
			</div>

			<div>
				<div class="py-0.5 flex w-full justify-between">
					<div class="self-center text-xs">{$i18n.t('Export Config')}</div>
					<button
						class="p-1 px-3 text-xs flex rounded-sm transition"
						on:click={async () => {
							const config = await exportConfig(localStorage.token);
							const blob = new Blob([JSON.stringify(config)], {
								type: 'application/json'
							});
							saveAs(blob, `config-${Date.now()}.json`);
						}}
						type="button"
					>
						<span class="self-center">{$i18n.t('Export')}</span>
					</button>
				</div>
			</div>
		</div>

		{#if $config?.features.enable_admin_export ?? true}
			<div>
				<div class="mb-1 text-sm font-medium">{$i18n.t('Database')}</div>

				<div>
					<div class="py-0.5 flex w-full justify-between">
						<div class="self-center text-xs">{$i18n.t('Download Database')}</div>
						<button
							class="p-1 px-3 text-xs flex rounded-sm transition"
							on:click={() => {
								downloadDatabase(localStorage.token).catch((error) => {
									toast.error(`${error}`);
								});
							}}
							type="button"
						>
							<span class="self-center">{$i18n.t('Download')}</span>
						</button>
					</div>
				</div>

				<div>
					<div class="py-0.5 flex w-full justify-between">
						<div class="self-center text-xs">{$i18n.t('Export All Chats (All Users)')}</div>
						<button
							class="p-1 px-3 text-xs flex rounded-sm transition"
							on:click={() => {
								exportAllUserChats();
							}}
							type="button"
						>
							<span class="self-center">{$i18n.t('Export')}</span>
						</button>
					</div>
				</div>

				<div>
					<div class="py-0.5 flex w-full justify-between">
						<div class="self-center text-xs">{$i18n.t('Export Users')}</div>
						<button
							class="p-1 px-3 text-xs flex rounded-sm transition"
							on:click={() => {
								exportUsers();
							}}
							type="button"
						>
							<span class="self-center">{$i18n.t('Export')}</span>
						</button>
					</div>
				</div>
			</div>
		{/if}

		<!-- Data Retention Section -->
		<hr class="border-gray-50 dark:border-gray-850/30 my-2" />

		<div>
			<div class="flex items-center justify-between mb-1">
				<div class="text-sm font-medium">{$i18n.t('Data Retention')}</div>
			</div>
			<div class="text-xs text-gray-500 mb-3">
				{$i18n.t('Automatically clean up inactive data after a retention period.')}
			</div>

			<div class="mb-3 space-y-3">
				<!-- Enable toggle -->
				<div class="flex w-full justify-between items-center">
					<div class="self-center text-xs font-medium">{$i18n.t('Enable data retention')}</div>
					<button
						type="button"
						class="relative inline-flex h-5 w-9 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none"
						class:bg-gray-200={retentionConfig.DATA_RETENTION_TTL_DAYS <= 0}
						class:dark:bg-gray-700={retentionConfig.DATA_RETENTION_TTL_DAYS <= 0}
						class:bg-emerald-500={retentionConfig.DATA_RETENTION_TTL_DAYS > 0}
						on:click={() => {
							retentionConfig.DATA_RETENTION_TTL_DAYS =
								retentionConfig.DATA_RETENTION_TTL_DAYS > 0 ? 0 : 730;
						}}
					>
						<span
							class="pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out"
							class:translate-x-4={retentionConfig.DATA_RETENTION_TTL_DAYS > 0}
							class:translate-x-0={retentionConfig.DATA_RETENTION_TTL_DAYS <= 0}
						/>
					</button>
				</div>

				{#if retentionConfig.DATA_RETENTION_TTL_DAYS > 0}
					<!-- Default retention period -->
					<div class="flex w-full justify-between items-center">
						<div class="self-center text-xs">{$i18n.t('Default retention period (days)')}</div>
						<input
							type="number"
							class="w-20 rounded py-1 px-2 text-xs bg-gray-50 dark:bg-gray-850 dark:text-gray-300"
							bind:value={retentionConfig.DATA_RETENTION_TTL_DAYS}
							min="1"
						/>
					</div>

					<!-- Per-entity overrides -->
					<div class="text-xs text-gray-500 mt-2 mb-1">
						{$i18n.t('Override per data type (leave empty to use default):')}
					</div>

					<div class="rounded border dark:border-gray-800 p-2 space-y-2">
						<div class="flex w-full justify-between items-center">
							<div class="self-center text-xs">{$i18n.t('Account deletion (user inactivity)')}</div>
							<div class="flex items-center gap-2">
								<input
									type="number"
									class="w-20 rounded py-1 px-2 text-xs bg-gray-50 dark:bg-gray-850 dark:text-gray-300"
									placeholder=""
									value={retentionConfig.USER_INACTIVITY_TTL_DAYS || ''}
									on:input={(e) => {
										retentionConfig.USER_INACTIVITY_TTL_DAYS = parseInt(e.target.value) || 0;
									}}
									min="0"
								/>
								<span class="text-xs text-gray-400 w-24 text-right">
									→ {effectiveDays(
										retentionConfig.USER_INACTIVITY_TTL_DAYS,
										retentionConfig.DATA_RETENTION_TTL_DAYS
									)}
									{$i18n.t('days')}
								</span>
							</div>
						</div>

						<div class="flex w-full justify-between items-center">
							<div class="self-center text-xs">{$i18n.t('Chats')}</div>
							<div class="flex items-center gap-2">
								<input
									type="number"
									class="w-20 rounded py-1 px-2 text-xs bg-gray-50 dark:bg-gray-850 dark:text-gray-300"
									placeholder=""
									value={retentionConfig.CHAT_RETENTION_TTL_DAYS || ''}
									on:input={(e) => {
										retentionConfig.CHAT_RETENTION_TTL_DAYS = parseInt(e.target.value) || 0;
									}}
									min="0"
								/>
								<span class="text-xs text-gray-400 w-24 text-right">
									→ {effectiveDays(
										retentionConfig.CHAT_RETENTION_TTL_DAYS,
										retentionConfig.DATA_RETENTION_TTL_DAYS
									)}
									{$i18n.t('days')}
								</span>
							</div>
						</div>

						<div class="flex w-full justify-between items-center">
							<div class="self-center text-xs">{$i18n.t('Knowledge bases')}</div>
							<div class="flex items-center gap-2">
								<input
									type="number"
									class="w-20 rounded py-1 px-2 text-xs bg-gray-50 dark:bg-gray-850 dark:text-gray-300"
									placeholder=""
									value={retentionConfig.KNOWLEDGE_RETENTION_TTL_DAYS || ''}
									on:input={(e) => {
										retentionConfig.KNOWLEDGE_RETENTION_TTL_DAYS = parseInt(e.target.value) || 0;
									}}
									min="0"
								/>
								<span class="text-xs text-gray-400 w-24 text-right">
									→ {effectiveDays(
										retentionConfig.KNOWLEDGE_RETENTION_TTL_DAYS,
										retentionConfig.DATA_RETENTION_TTL_DAYS
									)}
									{$i18n.t('days')}
								</span>
							</div>
						</div>
					</div>

					<!-- Warning email section (only visible when email is configured) -->
					{#if $config?.features?.enable_email_invites}
						<div class="text-xs text-gray-500 mt-2 mb-1">
							{$i18n.t('Warning emails')}
						</div>

						<div class="rounded border dark:border-gray-800 p-2 space-y-2">
							<div class="flex w-full justify-between items-center">
								<div class="self-center text-xs">{$i18n.t('Send warning emails')}</div>
								<button
									type="button"
									class="relative inline-flex h-5 w-9 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none"
									class:bg-gray-200={!retentionConfig.ENABLE_RETENTION_WARNING_EMAIL}
									class:dark:bg-gray-700={!retentionConfig.ENABLE_RETENTION_WARNING_EMAIL}
									class:bg-emerald-500={retentionConfig.ENABLE_RETENTION_WARNING_EMAIL}
									on:click={() => {
										retentionConfig.ENABLE_RETENTION_WARNING_EMAIL =
											!retentionConfig.ENABLE_RETENTION_WARNING_EMAIL;
									}}
								>
									<span
										class="pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out"
										class:translate-x-4={retentionConfig.ENABLE_RETENTION_WARNING_EMAIL}
										class:translate-x-0={!retentionConfig.ENABLE_RETENTION_WARNING_EMAIL}
									/>
								</button>
							</div>

							{#if retentionConfig.ENABLE_RETENTION_WARNING_EMAIL}
								<div class="flex w-full justify-between items-center">
									<div class="self-center text-xs">{$i18n.t('Days before deletion')}</div>
									<input
										type="number"
										class="w-20 rounded py-1 px-2 text-xs bg-gray-50 dark:bg-gray-850 dark:text-gray-300"
										bind:value={retentionConfig.DATA_RETENTION_WARNING_DAYS}
										min="1"
									/>
								</div>
							{/if}
						</div>
					{/if}
				{/if}

				<div class="flex justify-end mt-2">
					<button
						class="px-3 py-1.5 text-xs font-medium rounded bg-emerald-600 hover:bg-emerald-700 text-white transition"
						on:click={() => {
							showRetentionConfirm = true;
						}}
					>
						{$i18n.t('Save')}
					</button>
				</div>
			</div>
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
								archiveConfig.enable_auto_archive_on_self_delete =
									!archiveConfig.enable_auto_archive_on_self_delete;
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
							<div
								class="p-2 border rounded dark:border-gray-800 flex items-center justify-between text-xs"
							>
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
</div>

<!-- Data Retention Confirmation Modal -->
{#if showRetentionConfirm}
	<div
		class="fixed inset-0 bg-black/30 dark:bg-black/60 flex items-center justify-center z-9999 overflow-y-auto"
	>
		<div class="bg-white dark:bg-gray-900 rounded-lg p-4 max-w-md w-full mx-4">
			<h3 class="text-sm font-medium mb-3">{$i18n.t('Confirm Data Retention Settings')}</h3>

			{#if retentionConfig.DATA_RETENTION_TTL_DAYS > 0}
				<p class="text-xs text-gray-500 mb-3">
					{$i18n.t('The following cleanup will run automatically every 24 hours:')}
				</p>
				<ul class="text-xs space-y-1.5 mb-4">
					<li class="flex items-start gap-2">
						<span class="text-red-500 mt-0.5">&#x2022;</span>
						<span
							>{$i18n.t('Inactive user accounts will be archived and deleted after {{days}} days', {
								days: effectiveDays(
									retentionConfig.USER_INACTIVITY_TTL_DAYS,
									retentionConfig.DATA_RETENTION_TTL_DAYS
								)
							})}</span
						>
					</li>
					<li class="flex items-start gap-2">
						<span class="text-amber-500 mt-0.5">&#x2022;</span>
						<span
							>{$i18n.t('Unused chats will be deleted after {{days}} days', {
								days: effectiveDays(
									retentionConfig.CHAT_RETENTION_TTL_DAYS,
									retentionConfig.DATA_RETENTION_TTL_DAYS
								)
							})}</span
						>
					</li>
					<li class="flex items-start gap-2">
						<span class="text-amber-500 mt-0.5">&#x2022;</span>
						<span
							>{$i18n.t('Unused knowledge bases will be deleted after {{days}} days', {
								days: effectiveDays(
									retentionConfig.KNOWLEDGE_RETENTION_TTL_DAYS,
									retentionConfig.DATA_RETENTION_TTL_DAYS
								)
							})}</span
						>
					</li>
					{#if retentionConfig.ENABLE_RETENTION_WARNING_EMAIL && $config?.features?.enable_email_invites}
						<li class="flex items-start gap-2">
							<span class="text-blue-500 mt-0.5">&#x2022;</span>
							<span
								>{$i18n.t('Warning emails will be sent {{days}} days before account deletion', {
									days: retentionConfig.DATA_RETENTION_WARNING_DAYS
								})}</span
							>
						</li>
					{/if}
				</ul>
				<p class="text-xs text-gray-400 mb-4">
					{$i18n.t('Admin accounts are never automatically deleted.')}
				</p>
			{:else}
				<p class="text-xs text-gray-500 mb-4">
					{$i18n.t('Data retention will be disabled. No automatic cleanup will run.')}
				</p>
			{/if}

			<div class="flex justify-end gap-2">
				<button
					class="px-3 py-1.5 text-xs font-medium rounded border dark:border-gray-700 hover:bg-gray-100 dark:hover:bg-gray-800 transition"
					on:click={() => {
						showRetentionConfirm = false;
					}}
				>
					{$i18n.t('Cancel')}
				</button>
				<button
					class="px-3 py-1.5 text-xs font-medium rounded text-white transition"
					class:bg-emerald-600={retentionConfig.DATA_RETENTION_TTL_DAYS > 0}
					class:hover:bg-emerald-700={retentionConfig.DATA_RETENTION_TTL_DAYS > 0}
					class:bg-gray-600={retentionConfig.DATA_RETENTION_TTL_DAYS <= 0}
					class:hover:bg-gray-700={retentionConfig.DATA_RETENTION_TTL_DAYS <= 0}
					on:click={async () => {
						showRetentionConfirm = false;
						await handleSaveRetentionConfig();
					}}
				>
					{$i18n.t('Confirm')}
				</button>
			</div>
		</div>
	</div>
{/if}

<!-- Archive Details Modal -->
{#if showArchiveModal}
	<div
		class="fixed inset-0 bg-black/30 dark:bg-black/60 flex items-center justify-center z-9999 overflow-y-auto"
	>
		<div
			class="bg-white dark:bg-gray-900 rounded-lg p-4 max-w-lg w-full mx-4 max-h-[70vh] overflow-y-auto"
		>
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
						<path
							stroke-linecap="round"
							stroke-linejoin="round"
							stroke-width="2"
							d="M6 18L18 6M6 6l12 12"
						/>
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
