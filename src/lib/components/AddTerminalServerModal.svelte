<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { getContext } from 'svelte';
	const i18n = getContext<any>('i18n');

	import Modal from '$lib/components/common/Modal.svelte';
	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import AccessControlModal from '$lib/components/workspace/common/AccessControlModal.svelte';
	import AccessButton from '$lib/components/common/AccessButton.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';

	import { getTerminalConfig } from '$lib/apis/terminal';

	export let show = false;
	export let edit = false;
	export let connection: any = null;

	export let onSubmit: Function = () => {};
	export let onDelete: () => void = () => {};

	let url = '';
	let key = '';
	let name = '';
	let id = '';
	let auth_type = 'bearer';
	let path = '/openapi.json';
	let enabled = false;
	let chatUploads: 'default' | 'filesystem' = 'default';
	let chatContextMode: 'default' | 'chat_id' | 'off' = 'default';
	let automationContextMode: 'default' | 'automation_id' | 'off' = 'default';
	let showAdvanced = false;
	let showOrchestratorAdvanced = false;
	let showAccessControlModal = false;
	let showDeleteConfirmDialog = false;
	let accessGrants: any[] = [];

	// Policy / auto-detect state
	let serverType: 'orchestrator' | 'terminal' | null = null;
	let verifying = false;
	let refreshing = false;
	let policyId = '';
	let policyImage = '';
	let policyEnvPairs: { key: string; value: string }[] = [];
	let policyCpu = '1';
	let policyMemory = '1Gi';
	let policyStorage = 'ephemeral';
	let policyStorageSize = '5Gi';
	let policyIdleTimeout = 30;
	let lifecycleJson = '{}';
	let refreshOnlyIdle = true;
	let refreshReset = false;
	let loadingPolicy = false;
	let policyLoadError = '';

	const inputClass =
		'bg-transparent outline-hidden placeholder:text-gray-300 dark:placeholder:text-gray-700';
	const selectClass =
		'bg-transparent pr-5 outline-hidden placeholder:text-gray-300 dark:placeholder:text-gray-700';

	const stringifyJson = (value: object | null | undefined) => {
		return JSON.stringify(value && Object.keys(value).length ? value : {}, null, 2);
	};

	const init = () => {
		if (connection) {
			id = connection?.id ?? '';
			url = connection.url;
			key = connection?.key ?? '';
			name = connection?.name ?? '';
			auth_type = connection?.auth_type ?? 'bearer';
			path = connection?.path ?? '/openapi.json';
			enabled = connection?.enabled ?? true;
			chatUploads = connection?.config?.chat_uploads === 'filesystem' ? 'filesystem' : 'default';
			accessGrants = connection?.config?.access_grants ?? [];

			// Restore policy state
			serverType = connection?.server_type ?? (connection?.policy_id ? 'orchestrator' : null);
			policyId = connection?.policy_id ?? '';
			const contexts = serverType === 'orchestrator' ? (connection?.config?.contexts ?? {}) : {};
			chatContextMode =
				contexts?.chat === false
					? 'off'
					: contexts?.chat?.context_id === 'chat_id'
						? 'chat_id'
						: 'default';
			automationContextMode =
				contexts?.automation === false
					? 'off'
					: contexts?.automation?.context_id === 'automation_id'
						? 'automation_id'
						: 'default';

			const p: Record<string, any> = {};
			policyImage = p.image ?? '';
			policyIdleTimeout = p.idle_timeout_minutes ?? 30;
			policyStorage = p.storage ? 'persistent' : 'ephemeral';
			policyStorageSize = p.storage ?? '5Gi';

			// Restore env pairs
			const env = p.env ?? {};
			policyEnvPairs = Object.entries(env).map(([k, v]) => ({ key: k, value: v as string }));

			// Restore resources
			policyCpu = p.cpu_limit ?? '1';
			policyMemory = p.memory_limit ?? '1Gi';
			lifecycleJson = stringifyJson({});
			refreshOnlyIdle = true;
			refreshReset = false;
			loadingPolicy = false;
			policyLoadError = '';
		} else {
			id = '';
			url = '';
			key = '';
			name = '';
			auth_type = 'bearer';
			path = '/openapi.json';
			enabled = false;
			chatUploads = 'default';
			accessGrants = [];
			chatContextMode = 'default';
			automationContextMode = 'default';

			serverType = null;
			policyId = '';
			policyImage = '';
			policyEnvPairs = [];
			policyCpu = '1';
			policyMemory = '1Gi';
			policyStorage = 'ephemeral';
			policyStorageSize = '5Gi';
			policyIdleTimeout = 30;
			lifecycleJson = '{}';
			refreshOnlyIdle = true;
			refreshReset = false;
			loadingPolicy = false;
			policyLoadError = '';
		}
	};

	$: if (show) {
		init();
	}

	const verifyHandler = async () => {
		const _url = url.replace(/\/$/, '');
		if (!_url) {
			toast.error($i18n.t('Please enter a valid URL'));
			return;
		}

		verifying = true;
		try {
			{
				// Direct connection: verify from browser
				const res = await getTerminalConfig(_url, key);
				if (res) {
					toast.success($i18n.t('Server connection verified'));
				} else {
					toast.error($i18n.t('Server connection failed'));
				}
			}
		} catch {
			serverType = null;
			toast.error($i18n.t('Server connection failed'));
		} finally {
			verifying = false;
		}
	};

	const parseJson = (label: string, value: string): object | null => {
		try {
			const parsed = JSON.parse(value || '{}');
			if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
				toast.error($i18n.t('{{label}} must be a JSON object', { label }));
				return null;
			}
			return parsed;
		} catch {
			toast.error($i18n.t('{{label}} contains invalid JSON', { label }));
			return null;
		}
	};

	const buildPolicyData = (): object => {
		const data: Record<string, any> = {};

		if (policyImage) data.image = policyImage;
		if (policyCpu) data.cpu_limit = policyCpu;
		if (policyMemory) data.memory_limit = policyMemory;

		if (policyStorage === 'persistent') {
			data.storage = policyStorageSize;
		}

		if (policyIdleTimeout > 0) {
			data.idle_timeout_minutes = policyIdleTimeout;
		}

		// Env vars
		const env: Record<string, string> = {};
		for (const pair of policyEnvPairs) {
			if (pair.key.trim()) {
				env[pair.key.trim()] = pair.value;
			}
		}
		if (Object.keys(env).length > 0) {
			data.env = env;
		}

		return data;
	};

	const submitHandler = async () => {
		if (url === '') {
			toast.error($i18n.t('Please enter a valid URL'));
			return;
		}

		// Remove trailing slash
		url = url.replace(/\/$/, '');
		// Bearer key whitespace breaks the terminal WebSocket auth (HTTP headers strip it, JSON doesn't)
		key = key.trim();
		if (loadingPolicy) {
			toast.error($i18n.t('Policy is still loading'));
			return;
		}
		if (policyLoadError) {
			toast.error($i18n.t('Failed to load policy: {{error}}', { error: policyLoadError }));
			return;
		}

		// Save policy to orchestrator if applicable
		let policyData = {};
		let lifecycleData = {};

		const contexts: Record<string, false | { context_id: string }> = {};
		if (chatContextMode === 'off') contexts.chat = false;
		else if (chatContextMode === 'chat_id') contexts.chat = { context_id: 'chat_id' };
		if (automationContextMode === 'off') contexts.automation = false;
		else if (automationContextMode === 'automation_id') {
			contexts.automation = { context_id: 'automation_id' };
		}
		const useContexts = false;
		const connectionConfig: Record<string, any> =
			connection?.config && typeof connection.config === 'object' ? { ...connection.config } : {};
		delete connectionConfig.access_grants;
		if (useContexts) connectionConfig.contexts = contexts;
		else delete connectionConfig.contexts;
		if (chatUploads === 'filesystem') connectionConfig.chat_uploads = 'filesystem';
		else delete connectionConfig.chat_uploads;

		const result = {
			...{},
			url,
			key,
			name,
			path,
			auth_type,
			enabled: enabled,
			config: connectionConfig,
			// Policy fields
			...(serverType ? { server_type: serverType } : {}),
			...(serverType === 'orchestrator' && policyId ? { policy_id: policyId } : {})
		};

		onSubmit(result);
		show = false;
	};
</script>

<Modal size="sm" bind:show>
	<div>
		<div class="flex justify-between dark:text-gray-100 px-5 pt-4 pb-2">
			<h1 class="text-lg font-medium self-center font-primary">
				{#if edit}
					{$i18n.t('Edit Terminal Connection')}
				{:else}
					{$i18n.t('Add Terminal Connection')}
				{/if}
			</h1>

			<button
				class="self-center"
				aria-label={$i18n.t('Close')}
				on:click={() => {
					show = false;
				}}
			>
				<XMark className={'size-5'} />
			</button>
		</div>

		<div class="flex flex-col md:flex-row w-full px-4 pb-4 md:space-x-4 dark:text-gray-200">
			<div class="flex flex-col w-full sm:flex-row sm:justify-center sm:space-x-6">
				<form class="flex flex-col w-full" on:submit|preventDefault={submitHandler}>
					<div class="px-1">
						<div class="flex gap-2">
							<div class="flex flex-col flex-1">
								<div class="flex justify-between mb-0.5">
									<label for="terminal-name" class={`text-xs text-gray-500`}
										>{$i18n.t('Name')}</label
									>
								</div>

								<div class="flex flex-1 items-center">
									<input
										id="terminal-name"
										class={`w-full flex-1 text-sm ${inputClass}`}
										type="text"
										bind:value={name}
										placeholder={$i18n.t('My Terminal')}
										autocomplete="off"
									/>
								</div>
							</div>
						</div>

						<div class="flex gap-2 mt-2">
							<div class="flex flex-col w-full">
								<div class="flex justify-between mb-0.5">
									<label for="terminal-url" class={`text-xs text-gray-500`}>{$i18n.t('URL')}</label>
								</div>

								<div class="flex flex-1 items-center">
									<input
										id="terminal-url"
										class={`w-full flex-1 text-sm ${inputClass}`}
										type="text"
										bind:value={url}
										placeholder="http://localhost:9900"
										required
										autocomplete="off"
									/>
								</div>
							</div>

							<Tooltip content={$i18n.t('Verify Connection')} className="self-end -mb-1">
								<button
									class="self-center p-1 bg-transparent hover:bg-gray-100 dark:hover:bg-gray-850 rounded-lg transition"
									on:click={() => {
										verifyHandler();
									}}
									type="button"
									disabled={verifying}
									aria-label={$i18n.t('Verify Connection')}
								>
									{#if verifying}
										<svg
											class="w-4 h-4 animate-spin"
											xmlns="http://www.w3.org/2000/svg"
											fill="none"
											viewBox="0 0 24 24"
										>
											<circle
												class="opacity-25"
												cx="12"
												cy="12"
												r="10"
												stroke="currentColor"
												stroke-width="4"
											></circle>
											<path
												class="opacity-75"
												fill="currentColor"
												d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
											></path>
										</svg>
									{:else}
										<svg
											xmlns="http://www.w3.org/2000/svg"
											viewBox="0 0 20 20"
											fill="currentColor"
											aria-hidden="true"
											class="w-4 h-4"
										>
											<path
												fill-rule="evenodd"
												d="M15.312 11.424a5.5 5.5 0 01-9.201 2.466l-.312-.311h2.433a.75.75 0 000-1.5H3.989a.75.75 0 00-.75.75v4.242a.75.75 0 001.5 0v-2.43l.31.31a7 7 0 0011.712-3.138.75.75 0 00-1.449-.39zm1.23-3.723a.75.75 0 00.219-.53V2.929a.75.75 0 00-1.5 0V5.36l-.31-.31A7 7 0 003.239 8.188a.75.75 0 101.448.389A5.5 5.5 0 0113.89 6.11l.311.31h-2.432a.75.75 0 000 1.5h4.243a.75.75 0 00.53-.219z"
												clip-rule="evenodd"
											/>
										</svg>
									{/if}
								</button>
							</Tooltip>
						</div>

						<div class="flex gap-2 mt-2">
							<div class="flex flex-col w-full">
								<div class="flex justify-between mb-0.5">
									<label for="terminal-chat-uploads" class={`text-xs text-gray-500`}
										>{$i18n.t('Chat Uploads')}</label
									>
								</div>
								<div class="flex flex-1 items-center">
									<select
										id="terminal-chat-uploads"
										class={`w-full text-sm ${selectClass}`}
										bind:value={chatUploads}
									>
										<option value="default">{$i18n.t('Default')}</option>
										<option value="filesystem">{$i18n.t('Filesystem')}</option>
									</select>
								</div>
							</div>
						</div>

						<!-- Policy section (orchestrator only, admin only) -->

						<div class="flex items-center justify-between">
							<button
								type="button"
								class="flex items-center gap-1 text-xs text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 transition mt-2"
								on:click={() => (showAdvanced = !showAdvanced)}
							>
								<svg
									xmlns="http://www.w3.org/2000/svg"
									viewBox="0 0 20 20"
									fill="currentColor"
									class="w-3 h-3 transition-transform {showAdvanced ? 'rotate-90' : ''}"
								>
									<path
										fill-rule="evenodd"
										d="M7.21 14.77a.75.75 0 01.02-1.06L11.168 10 7.23 6.29a.75.75 0 111.04-1.08l4.5 4.25a.75.75 0 010 1.08l-4.5 4.25a.75.75 0 01-1.06-.02z"
										clip-rule="evenodd"
									/>
								</svg>
								{$i18n.t('Advanced')}
							</button>
						</div>

						{#if showAdvanced}
							<div class="flex gap-2 mt-2">
								<div class="flex flex-col w-full">
									<div class="flex justify-between items-center mb-0.5">
										<div class="flex gap-2 items-center">
											<div class={`text-xs text-gray-500`}>
												{$i18n.t('OpenAPI Spec')}
											</div>
										</div>
									</div>

									<div class="flex gap-2">
										<div class="flex flex-1 items-center">
											<div class="flex-1 flex items-center">
												<label for="openapi-path" class="sr-only"
													>{$i18n.t('openapi.json URL or Path')}</label
												>
												<input
													class={`w-full text-sm ${inputClass}`}
													type="text"
													id="openapi-path"
													bind:value={path}
													placeholder={$i18n.t('openapi.json URL or Path')}
													autocomplete="off"
													required
												/>
											</div>
										</div>
									</div>

									<div class={`text-xs mt-1 text-gray-500`}>
										{$i18n.t(`WebUI will make requests to "{{url}}"`, {
											url: path.includes('://')
												? path
												: `${url}${path.startsWith('/') ? '' : '/'}${path}`
										})}
									</div>
								</div>
							</div>
						{/if}

						<div class="flex gap-2 mt-2">
							<div class="flex flex-col w-full">
								<div class="flex justify-between items-center">
									<div class="flex gap-2 items-center">
										<div class={`text-xs text-gray-500`}>
											{$i18n.t('Auth')}
										</div>
									</div>
								</div>

								<div class="flex gap-2">
									<div class="flex-shrink-0 self-start">
										<select class={`w-full text-sm ${selectClass}`} bind:value={auth_type}>
											<option value="none">{$i18n.t('None')}</option>
											<option value="bearer">{$i18n.t('Bearer')}</option>
										</select>
									</div>

									<div class="flex flex-1 items-center">
										{#if auth_type === 'bearer'}
											<SensitiveInput
												bind:value={key}
												placeholder={$i18n.t('API Key')}
												required={false}
											/>
										{:else if auth_type === 'none'}
											<div class={`text-xs self-center translate-y-[1px] text-gray-500`}>
												{$i18n.t('No authentication')}
											</div>
										{:else if auth_type === 'session'}
											<div class={`text-xs self-center translate-y-[1px] text-gray-500`}>
												{$i18n.t('Forwards system user session credentials to authenticate')}
											</div>
										{:else if auth_type === 'system_oauth'}
											<div class={`text-xs self-center translate-y-[1px] text-gray-500`}>
												{$i18n.t('Forwards system user OAuth access token to authenticate')}
											</div>
										{/if}
									</div>
								</div>
							</div>
						</div>

						<div class="flex justify-between items-center pt-3 text-sm font-medium">
							<div>
								{#if edit}
									<button
										class="px-1 py-1.5 text-sm font-medium text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 hover:underline transition"
										type="button"
										on:click={() => {
											showDeleteConfirmDialog = true;
										}}
									>
										{$i18n.t('Delete')}
									</button>
								{/if}
							</div>

							<button
								class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 disabled:opacity-50 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full flex flex-row space-x-1 items-center"
								type="submit"
								disabled={loadingPolicy || !!policyLoadError}
							>
								{$i18n.t('Save')}
							</button>
						</div>
					</div>
				</form>
			</div>
		</div>
	</div>
</Modal>

<AccessControlModal bind:show={showAccessControlModal} bind:accessGrants />

<ConfirmDialog
	bind:show={showDeleteConfirmDialog}
	message={$i18n.t(
		'Are you sure you want to delete this connection? This action cannot be undone.'
	)}
	confirmLabel={$i18n.t('Delete')}
	on:confirm={() => {
		onDelete();
		show = false;
	}}
/>
