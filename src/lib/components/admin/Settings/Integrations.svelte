<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import { toast } from 'svelte-sonner';

	import { getIntegrationsConfig, setIntegrationsConfig } from '$lib/apis/configs';
	import { searchUsers } from '$lib/apis/users';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Badge from '$lib/components/common/Badge.svelte';

	const i18n = getContext('i18n');

	export let saveHandler: Function;

	let loading = true;
	let saving = false;

	// Provider registry: slug -> config
	let providers: Record<string, any> = {};

	// Edit/add state
	let editingSlug: string | null = null;
	let showForm = false;
	let form = {
		slug: '',
		name: '',
		description: '',
		badge_type: 'info',
		max_files_per_kb: 250,
		max_documents_per_request: 50,
		service_account_id: '',
		custom_metadata_fields: [] as { key: string; label: string; required: boolean }[]
	};

	// User search for service account
	let userSearchQuery = '';
	let userSearchResults: any[] = [];
	let selectedUser: any = null;
	let showUserSearch = false;

	// Confirm delete
	let deleteConfirmSlug: string | null = null;

	// Expanded provider panel: 'api' | 'edit' | null per slug
	let expandedPanel: Record<string, 'api' | 'edit'> = {};

	onMount(async () => {
		try {
			const config = await getIntegrationsConfig(localStorage.token);
			if (config) {
				providers = config.providers || {};
			}
		} catch (err) {
			toast.error(`${err}`);
		}
		loading = false;
	});

	const handleSave = async () => {
		saving = true;
		try {
			await setIntegrationsConfig(localStorage.token, { providers });
			saveHandler();
		} catch (err) {
			toast.error(`${err}`);
		}
		saving = false;
	};

	function resetForm() {
		form = {
			slug: '',
			name: '',
			description: '',
			badge_type: 'info',
			max_files_per_kb: 250,
			max_documents_per_request: 50,
			service_account_id: '',
			custom_metadata_fields: []
		};
		selectedUser = null;
		userSearchQuery = '';
		userSearchResults = [];
		showUserSearch = false;
	}

	function startAdd() {
		editingSlug = null;
		resetForm();
		showForm = true;
	}

	function startEdit(slug: string) {
		// Toggle: if already showing edit for this slug, close it
		if (expandedPanel[slug] === 'edit') {
			delete expandedPanel[slug];
			expandedPanel = expandedPanel;
			showForm = false;
			editingSlug = null;
			return;
		}

		editingSlug = slug;
		const p = providers[slug];
		form = {
			slug,
			name: p.name || '',
			description: p.description || '',
			badge_type: p.badge_type || 'info',
			max_files_per_kb: p.max_files_per_kb || 250,
			max_documents_per_request: p.max_documents_per_request || 50,
			service_account_id: p.service_account_id || '',
			custom_metadata_fields: p.custom_metadata_fields || []
		};
		selectedUser = null;
		expandedPanel[slug] = 'edit';
		expandedPanel = expandedPanel;
		showForm = true;
	}

	function toggleApiExample(slug: string) {
		if (expandedPanel[slug] === 'api') {
			delete expandedPanel[slug];
			expandedPanel = expandedPanel;
		} else {
			// Close edit if open for this slug
			if (expandedPanel[slug] === 'edit') {
				showForm = false;
				editingSlug = null;
			}
			expandedPanel[slug] = 'api';
			expandedPanel = expandedPanel;
		}
	}

	function generateSlug(name: string): string {
		return name
			.toLowerCase()
			.replace(/[^a-z0-9]+/g, '-')
			.replace(/^-|-$/g, '');
	}

	async function applyForm() {
		const slug = editingSlug || form.slug || generateSlug(form.name);
		if (!slug) {
			toast.error('Slug is required');
			return;
		}
		if (!form.name) {
			toast.error('Name is required');
			return;
		}
		if (!editingSlug && providers[slug]) {
			toast.error(`Provider with slug "${slug}" already exists`);
			return;
		}

		const { slug: _slug, ...config } = form;
		providers[slug] = config;

		// If editing and slug changed, remove old
		if (editingSlug && editingSlug !== slug) {
			delete providers[editingSlug];
		}

		providers = providers;
		showForm = false;
		delete expandedPanel[editingSlug || slug];
		expandedPanel = expandedPanel;
		editingSlug = null;

		// Auto-save to backend
		await handleSave();
	}

	async function removeProvider(slug: string) {
		delete providers[slug];
		delete expandedPanel[slug];
		providers = providers;
		expandedPanel = expandedPanel;
		deleteConfirmSlug = null;

		// Auto-save to backend
		await handleSave();
	}

	async function handleUserSearch() {
		if (userSearchQuery.length < 1) {
			userSearchResults = [];
			return;
		}
		try {
			const res = await searchUsers(localStorage.token, userSearchQuery);
			userSearchResults = res?.users || [];
		} catch {
			userSearchResults = [];
		}
	}

	function selectUser(user: any) {
		form.service_account_id = user.id;
		selectedUser = user;
		showUserSearch = false;
		userSearchQuery = '';
		userSearchResults = [];
	}
</script>

<div class="flex flex-col h-full text-sm">
	<div class="space-y-3 overflow-y-scroll scrollbar-hidden h-full pr-1.5">
		{#if loading}
			<div class="flex justify-center py-8">
				<Spinner />
			</div>
		{:else}
			<div class="space-y-3">
				<div class="flex justify-between items-center">
					<div class="font-medium">{$i18n.t('Integration Providers')}</div>
					<button
						class="px-3 py-1 text-xs font-medium border border-gray-200 dark:border-gray-700 hover:bg-gray-100 dark:hover:bg-gray-800 transition rounded-full"
						type="button"
						on:click={startAdd}
					>
						+ {$i18n.t('Add Provider')}
					</button>
				</div>

				<!-- Provider List -->
				{#if Object.keys(providers).length === 0 && !showForm}
					<div class="text-center text-gray-400 py-8">
						{$i18n.t('No integration providers configured')}
					</div>
				{/if}

				{#each Object.entries(providers) as [slug, provider]}
					<div class="border border-gray-200 dark:border-gray-700 rounded-lg">
						<!-- Provider header row -->
						<div class="flex items-center justify-between p-3">
							<div class="flex items-center gap-3">
								<Badge type={provider.badge_type || 'info'} content={provider.name} />
								<div class="text-xs text-gray-500">{slug}</div>

								{#if provider.service_account_id}
									<div class="text-xs text-green-600 dark:text-green-400">
										{$i18n.t('Service account bound')}
									</div>
								{:else}
									<div class="text-xs text-yellow-600 dark:text-yellow-400">
										{$i18n.t('No service account')}
									</div>
								{/if}
							</div>
							<div class="flex items-center gap-1">
								<button
									class="px-2 py-0.5 text-xs rounded {expandedPanel[slug] === 'api'
										? 'bg-gray-200 dark:bg-gray-700 text-gray-800 dark:text-gray-200'
										: 'text-gray-500 hover:text-gray-700 dark:hover:text-gray-300'}"
									type="button"
									on:click={() => toggleApiExample(slug)}
								>
									API
								</button>
								<button
									class="px-2 py-0.5 text-xs rounded {expandedPanel[slug] === 'edit'
										? 'bg-gray-200 dark:bg-gray-700 text-gray-800 dark:text-gray-200'
										: 'text-gray-500 hover:text-gray-700 dark:hover:text-gray-300'}"
									type="button"
									on:click={() => startEdit(slug)}
								>
									{$i18n.t('Edit')}
								</button>
								{#if deleteConfirmSlug === slug}
									<button
										class="px-2 py-0.5 text-xs text-red-600 font-medium"
										type="button"
										on:click={() => removeProvider(slug)}
									>
										{$i18n.t('Confirm')}
									</button>
									<button
										class="px-2 py-0.5 text-xs text-gray-500"
										type="button"
										on:click={() => (deleteConfirmSlug = null)}
									>
										{$i18n.t('Cancel')}
									</button>
								{:else}
									<button
										class="px-2 py-0.5 text-xs text-red-500 hover:text-red-700"
										type="button"
										on:click={() => (deleteConfirmSlug = slug)}
									>
										{$i18n.t('Remove')}
									</button>
								{/if}
							</div>
						</div>

						<!-- Expanded panel: API example -->
						{#if expandedPanel[slug] === 'api'}
							{@const exampleData = JSON.stringify({ collection: { source_id: 'my-collection-123', name: 'My Collection', data_type: 'parsed_text', access_control: null }, documents: [{ source_id: 'doc-1', filename: 'example.txt', text: 'Document content here...', title: 'Example Document' }] }, null, 2)}
							<div
								class="px-3 pb-3 pt-0"
							>
								<div
									class="p-3 bg-gray-50 dark:bg-gray-800 rounded-lg text-xs font-mono overflow-x-auto"
								>
									<div class="text-gray-500 mb-2">{$i18n.t('Example: Ingest documents')}</div>
									<pre class="whitespace-pre-wrap">curl -X POST {window.location.origin}/api/v1/integrations/ingest \
  -H "Authorization: Bearer sk-YOUR-API-KEY" \
  -F 'data={exampleData}'</pre>
									<div class="text-gray-400 mt-2 text-[10px]">
										data_type: "parsed_text" | "chunked_text" | "full_documents"
									</div>
									<div class="text-gray-400 mt-1 text-[10px]">
										access_control: null = public, {'{}'} = private, {'{"read": {"group_ids": [...]}}'} = custom
									</div>
								</div>
							</div>
						{/if}

						<!-- Expanded panel: Edit form (inline) -->
						{#if expandedPanel[slug] === 'edit' && editingSlug === slug}
							<div class="px-3 pb-3 pt-0">
								<div class="p-4 bg-gray-50 dark:bg-gray-800 rounded-lg space-y-3">
									<div class="font-medium text-xs text-gray-500 uppercase tracking-wide">
										{$i18n.t('Edit Provider')}
									</div>

									<div class="grid grid-cols-2 gap-3">
										<div>
											<div class="mb-1 text-xs text-gray-500">{$i18n.t('Name')}</div>
											<input
												class="w-full text-sm bg-transparent outline-hidden border border-gray-200 dark:border-gray-700 rounded px-2 py-1"
												type="text"
												bind:value={form.name}
												placeholder={$i18n.t('Integration')}
											/>
										</div>
										<div>
											<div class="mb-1 text-xs text-gray-500">{$i18n.t('Slug')}</div>
											<input
												class="w-full text-sm bg-transparent outline-hidden border border-gray-200 dark:border-gray-700 rounded px-2 py-1 opacity-50"
												type="text"
												bind:value={form.slug}
												placeholder={$i18n.t('integration')}
												disabled
											/>
										</div>
									</div>

									<div>
										<div class="mb-1 text-xs text-gray-500">{$i18n.t('Description')}</div>
										<input
											class="w-full text-sm bg-transparent outline-hidden border border-gray-200 dark:border-gray-700 rounded px-2 py-1"
											type="text"
											bind:value={form.description}
											placeholder={$i18n.t('Document pipeline integration')}
										/>
									</div>

									<div class="grid grid-cols-2 gap-3">
										<div>
											<div class="mb-1 text-xs text-gray-500">{$i18n.t('Badge Type')}</div>
											<select
												class="w-full text-sm bg-transparent outline-hidden border border-gray-200 dark:border-gray-700 rounded px-2 py-1"
												bind:value={form.badge_type}
											>
												<option value="info">Info (Blue)</option>
												<option value="success">Success (Green)</option>
												<option value="warning">Warning (Yellow)</option>
												<option value="error">Error (Red)</option>
												<option value="muted">Muted (Gray)</option>
											</select>
										</div>
										<div>
											<div class="mb-1 text-xs text-gray-500">{$i18n.t('Badge Preview')}</div>
											<div class="pt-1">
												<Badge type={form.badge_type} content={form.name || 'Preview'} />
											</div>
										</div>
									</div>

									<div class="grid grid-cols-2 gap-3">
										<div>
											<div class="mb-1 text-xs text-gray-500">
												{$i18n.t('Max Files Per Knowledge Base')}
											</div>
											<input
												class="w-full text-sm bg-transparent outline-hidden border border-gray-200 dark:border-gray-700 rounded px-2 py-1"
												type="number"
												bind:value={form.max_files_per_kb}
												min="1"
												max="10000"
											/>
										</div>
										<div>
											<div class="mb-1 text-xs text-gray-500">
												{$i18n.t('Max Documents Per Request')}
											</div>
											<input
												class="w-full text-sm bg-transparent outline-hidden border border-gray-200 dark:border-gray-700 rounded px-2 py-1"
												type="number"
												bind:value={form.max_documents_per_request}
												min="1"
												max="1000"
											/>
										</div>
									</div>

									<!-- Custom Metadata Fields -->
									<div>
										<div class="flex items-center justify-between mb-1">
											<div class="text-xs text-gray-500">
												{$i18n.t('Custom Metadata Fields')}
											</div>
											<button
												class="text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
												type="button"
												on:click={() => {
													form.custom_metadata_fields = [
														...form.custom_metadata_fields,
														{ key: '', label: '', required: false }
													];
												}}
											>
												+ {$i18n.t('Add Field')}
											</button>
										</div>
										{#if form.custom_metadata_fields.length === 0}
											<div class="text-xs text-gray-400 py-1">
												{$i18n.t(
													'No custom metadata fields. Documents will use the default schema.'
												)}
											</div>
										{/if}
										{#each form.custom_metadata_fields as field, i}
											<div class="flex items-center gap-2 mb-1.5">
												<input
													class="flex-1 text-sm bg-transparent outline-hidden border border-gray-200 dark:border-gray-700 rounded px-2 py-1"
													type="text"
													bind:value={field.key}
													placeholder="field_key"
												/>
												<input
													class="flex-1 text-sm bg-transparent outline-hidden border border-gray-200 dark:border-gray-700 rounded px-2 py-1"
													type="text"
													bind:value={field.label}
													placeholder="Display Label"
												/>
												<label
													class="flex items-center gap-1 text-xs text-gray-500 whitespace-nowrap"
												>
													<input type="checkbox" bind:checked={field.required} />
													{$i18n.t('Required')}
												</label>
												<button
													class="text-xs text-red-500 hover:text-red-700"
													type="button"
													on:click={() => {
														form.custom_metadata_fields =
															form.custom_metadata_fields.filter((_, idx) => idx !== i);
													}}
												>
													&times;
												</button>
											</div>
										{/each}
									</div>

									<!-- Service Account -->
									<div>
										<div class="mb-1 text-xs text-gray-500">
											{$i18n.t('Service Account')}
										</div>
										{#if form.service_account_id && !showUserSearch}
											<div class="flex items-center gap-2">
												<div
													class="flex-1 text-sm bg-transparent border border-gray-200 dark:border-gray-700 rounded px-2 py-1"
												>
													{#if selectedUser}
														{selectedUser.name} ({selectedUser.email})
													{:else}
														ID: {form.service_account_id}
													{/if}
												</div>
												<button
													class="text-xs text-gray-500 hover:text-gray-700"
													type="button"
													on:click={() => {
														showUserSearch = true;
													}}
												>
													{$i18n.t('Change')}
												</button>
												<button
													class="text-xs text-red-500 hover:text-red-700"
													type="button"
													on:click={() => {
														form.service_account_id = '';
														selectedUser = null;
													}}
												>
													{$i18n.t('Remove')}
												</button>
											</div>
										{:else}
											<div class="relative">
												<input
													class="w-full text-sm bg-transparent outline-hidden border border-gray-200 dark:border-gray-700 rounded px-2 py-1"
													type="text"
													bind:value={userSearchQuery}
													placeholder={$i18n.t('Search users...')}
													on:input={handleUserSearch}
													on:focus={() => {
														showUserSearch = true;
													}}
												/>
												{#if showUserSearch && userSearchResults.length > 0}
													<div
														class="absolute z-10 w-full mt-1 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-lg max-h-40 overflow-y-auto"
													>
														{#each userSearchResults as user}
															<button
																class="w-full text-left px-3 py-2 text-sm hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-2"
																type="button"
																on:click={() => selectUser(user)}
															>
																<div class="font-medium">{user.name}</div>
																<div class="text-xs text-gray-500">{user.email}</div>
															</button>
														{/each}
													</div>
												{/if}
											</div>
										{/if}
									</div>

									<div class="flex justify-end gap-2 pt-2">
										<button
											class="px-3 py-1 text-xs font-medium border border-gray-200 dark:border-gray-700 hover:bg-gray-100 dark:hover:bg-gray-800 transition rounded-full"
											type="button"
											on:click={() => {
												delete expandedPanel[slug];
												expandedPanel = expandedPanel;
												showForm = false;
												editingSlug = null;
											}}
										>
											{$i18n.t('Cancel')}
										</button>
										<button
											class="px-3 py-1 text-xs font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full"
											type="button"
											on:click={applyForm}
										>
											{$i18n.t('Update')}
										</button>
									</div>
								</div>
							</div>
						{/if}
					</div>
				{/each}

				<!-- Add New Provider Form (standalone, not inside a provider card) -->
				{#if showForm && !editingSlug}
					<div class="p-4 border border-gray-200 dark:border-gray-700 rounded-lg space-y-3">
						<div class="font-medium text-xs text-gray-500 uppercase tracking-wide">
							{$i18n.t('Add Provider')}
						</div>

						<div class="grid grid-cols-2 gap-3">
							<div>
								<div class="mb-1 text-xs text-gray-500">{$i18n.t('Name')}</div>
								<input
									class="w-full text-sm bg-transparent outline-hidden border border-gray-200 dark:border-gray-700 rounded px-2 py-1"
									type="text"
									bind:value={form.name}
									placeholder={$i18n.t('Integration')}
									on:input={() => {
										form.slug = generateSlug(form.name);
									}}
								/>
							</div>
							<div>
								<div class="mb-1 text-xs text-gray-500">{$i18n.t('Slug')}</div>
								<input
									class="w-full text-sm bg-transparent outline-hidden border border-gray-200 dark:border-gray-700 rounded px-2 py-1"
									type="text"
									bind:value={form.slug}
									placeholder={$i18n.t('integration')}
								/>
							</div>
						</div>

						<div>
							<div class="mb-1 text-xs text-gray-500">{$i18n.t('Description')}</div>
							<input
								class="w-full text-sm bg-transparent outline-hidden border border-gray-200 dark:border-gray-700 rounded px-2 py-1"
								type="text"
								bind:value={form.description}
								placeholder={$i18n.t('Document pipeline integration')}
							/>
						</div>

						<div class="grid grid-cols-2 gap-3">
							<div>
								<div class="mb-1 text-xs text-gray-500">{$i18n.t('Badge Type')}</div>
								<select
									class="w-full text-sm bg-transparent outline-hidden border border-gray-200 dark:border-gray-700 rounded px-2 py-1"
									bind:value={form.badge_type}
								>
									<option value="info">Info (Blue)</option>
									<option value="success">Success (Green)</option>
									<option value="warning">Warning (Yellow)</option>
									<option value="error">Error (Red)</option>
									<option value="muted">Muted (Gray)</option>
								</select>
							</div>
							<div>
								<div class="mb-1 text-xs text-gray-500">{$i18n.t('Badge Preview')}</div>
								<div class="pt-1">
									<Badge type={form.badge_type} content={form.name || 'Preview'} />
								</div>
							</div>
						</div>

						<div class="grid grid-cols-2 gap-3">
							<div>
								<div class="mb-1 text-xs text-gray-500">
									{$i18n.t('Max Files Per Knowledge Base')}
								</div>
								<input
									class="w-full text-sm bg-transparent outline-hidden border border-gray-200 dark:border-gray-700 rounded px-2 py-1"
									type="number"
									bind:value={form.max_files_per_kb}
									min="1"
									max="10000"
								/>
							</div>
							<div>
								<div class="mb-1 text-xs text-gray-500">
									{$i18n.t('Max Documents Per Request')}
								</div>
								<input
									class="w-full text-sm bg-transparent outline-hidden border border-gray-200 dark:border-gray-700 rounded px-2 py-1"
									type="number"
									bind:value={form.max_documents_per_request}
									min="1"
									max="1000"
								/>
							</div>
						</div>

						<!-- Custom Metadata Fields -->
						<div>
							<div class="flex items-center justify-between mb-1">
								<div class="text-xs text-gray-500">
									{$i18n.t('Custom Metadata Fields')}
								</div>
								<button
									class="text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
									type="button"
									on:click={() => {
										form.custom_metadata_fields = [
											...form.custom_metadata_fields,
											{ key: '', label: '', required: false }
										];
									}}
								>
									+ {$i18n.t('Add Field')}
								</button>
							</div>
							{#if form.custom_metadata_fields.length === 0}
								<div class="text-xs text-gray-400 py-1">
									{$i18n.t(
										'No custom metadata fields. Documents will use the default schema.'
									)}
								</div>
							{/if}
							{#each form.custom_metadata_fields as field, i}
								<div class="flex items-center gap-2 mb-1.5">
									<input
										class="flex-1 text-sm bg-transparent outline-hidden border border-gray-200 dark:border-gray-700 rounded px-2 py-1"
										type="text"
										bind:value={field.key}
										placeholder="field_key"
									/>
									<input
										class="flex-1 text-sm bg-transparent outline-hidden border border-gray-200 dark:border-gray-700 rounded px-2 py-1"
										type="text"
										bind:value={field.label}
										placeholder="Display Label"
									/>
									<label
										class="flex items-center gap-1 text-xs text-gray-500 whitespace-nowrap"
									>
										<input type="checkbox" bind:checked={field.required} />
										{$i18n.t('Required')}
									</label>
									<button
										class="text-xs text-red-500 hover:text-red-700"
										type="button"
										on:click={() => {
											form.custom_metadata_fields =
												form.custom_metadata_fields.filter((_, idx) => idx !== i);
										}}
									>
										&times;
									</button>
								</div>
							{/each}
						</div>

						<!-- Service Account -->
						<div>
							<div class="mb-1 text-xs text-gray-500">{$i18n.t('Service Account')}</div>
							{#if form.service_account_id && !showUserSearch}
								<div class="flex items-center gap-2">
									<div
										class="flex-1 text-sm bg-transparent border border-gray-200 dark:border-gray-700 rounded px-2 py-1"
									>
										{#if selectedUser}
											{selectedUser.name} ({selectedUser.email})
										{:else}
											ID: {form.service_account_id}
										{/if}
									</div>
									<button
										class="text-xs text-gray-500 hover:text-gray-700"
										type="button"
										on:click={() => {
											showUserSearch = true;
										}}
									>
										{$i18n.t('Change')}
									</button>
									<button
										class="text-xs text-red-500 hover:text-red-700"
										type="button"
										on:click={() => {
											form.service_account_id = '';
											selectedUser = null;
										}}
									>
										{$i18n.t('Remove')}
									</button>
								</div>
							{:else}
								<div class="relative">
									<input
										class="w-full text-sm bg-transparent outline-hidden border border-gray-200 dark:border-gray-700 rounded px-2 py-1"
										type="text"
										bind:value={userSearchQuery}
										placeholder={$i18n.t('Search users...')}
										on:input={handleUserSearch}
										on:focus={() => {
											showUserSearch = true;
										}}
									/>
									{#if showUserSearch && userSearchResults.length > 0}
										<div
											class="absolute z-10 w-full mt-1 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-lg max-h-40 overflow-y-auto"
										>
											{#each userSearchResults as user}
												<button
													class="w-full text-left px-3 py-2 text-sm hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-2"
													type="button"
													on:click={() => selectUser(user)}
												>
													<div class="font-medium">{user.name}</div>
													<div class="text-xs text-gray-500">{user.email}</div>
												</button>
											{/each}
										</div>
									{/if}
								</div>
							{/if}
						</div>

						<div class="flex justify-end gap-2 pt-2">
							<button
								class="px-3 py-1 text-xs font-medium border border-gray-200 dark:border-gray-700 hover:bg-gray-100 dark:hover:bg-gray-800 transition rounded-full"
								type="button"
								on:click={() => {
									showForm = false;
								}}
							>
								{$i18n.t('Cancel')}
							</button>
							<button
								class="px-3 py-1 text-xs font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full"
								type="button"
								on:click={applyForm}
							>
								{$i18n.t('Add')}
							</button>
						</div>
					</div>
				{/if}
			</div>
		{/if}
	</div>


</div>
