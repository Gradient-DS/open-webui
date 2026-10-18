<script lang="ts">
	import DOMPurify from 'dompurify';
	import { v4 as uuidv4 } from 'uuid';

	import { getBackendConfig, getVersionUpdates } from '$lib/apis';
	import {
		getAdminConfig,
		getLdapConfig,
		getLdapServer,
		updateAdminConfig,
		updateLdapConfig,
		updateLdapServer
	} from '$lib/apis/auths';
	import { getBanners, setBanners } from '$lib/apis/configs';
	import { getGroups } from '$lib/apis/groups';
	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';
	import InterfaceSettings from '$lib/components/common/InterfaceSettings.svelte';
	import SettingsSelect from '$lib/components/common/SettingsSelect.svelte';
	import LanguageModeSelect from '$lib/components/common/LanguageModeSelect.svelte';
	import Switch from '$lib/components/common/Switch.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import BuildVersion from '$lib/components/common/BuildVersion.svelte';
	import { WEBUI_BASE_URL, WEBUI_BUILD_CHANNEL, WEBUI_VERSION } from '$lib/constants';
	import { isFeatureEnabled } from '$lib/utils/features';
	import { banners as _banners, config, showChangelog } from '$lib/stores';
	import type { Banner } from '$lib/types';
	import { compareVersion } from '$lib/utils';
	import { onMount, getContext } from 'svelte';
	import { toast } from 'svelte-sonner';
	import Textarea from '$lib/components/common/Textarea.svelte';
	import Banners from './Interface/Banners.svelte';
	import Events from './Events.svelte';
	import AdminSettingField from './AdminSettingField.svelte';
	import AdminSettingRow from './AdminSettingRow.svelte';
	import AdminSettingSection from './AdminSettingSection.svelte';
	import Plus from '$lib/components/icons/Plus.svelte';
	import I18nSettings from './I18nSettings.svelte';
	import { updateI18n } from '$lib/i18n';
	import { entriesToI18n, i18nToEntries, type I18nEntry } from '$lib/utils/translationDictionary';

	const i18n: any = getContext('i18n');

	export let saveHandler: Function;

	let updateAvailable: boolean | null = null;
	let version = {
		current: WEBUI_VERSION,
		latest: ''
	};

	let adminConfig: any = null;
	let defaultInterfaceSettings: Record<string, any> = {};
	let showUserUiDefaults = false;
	let uiI18nEntries: I18nEntry[] = [];
	let saving = false;

	let groups = [];

	let banners: Banner[] = [];

	// LDAP
	let ENABLE_LDAP = false;
	let LDAP_SERVER = {
		label: '',
		host: '',
		port: '',
		attribute_for_mail: 'mail',
		attribute_for_username: 'uid',
		app_dn: '',
		app_dn_password: '',
		search_base: '',
		search_filters: '',
		use_tls: false,
		certificate_path: '',
		ciphers: ''
	};

	let bannerLocale = '';
	const inputClass =
		'w-full h-7 rounded-lg border border-gray-100/50 bg-gray-50/40 px-2 text-xs text-gray-700 outline-hidden transition-colors placeholder:text-gray-300 focus:border-blue-400 dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-300 dark:placeholder:text-gray-700 dark:focus:border-blue-500';
	const textareaClass =
		'w-full rounded-lg border border-gray-100/50 bg-gray-50/40 px-2 py-1.5 text-xs text-gray-700 outline-hidden transition-colors placeholder:text-gray-300 focus:border-blue-400 dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-300 dark:placeholder:text-gray-700 dark:focus:border-blue-500';
	const checkForVersionUpdates = async () => {
		updateAvailable = null;
		version = await getVersionUpdates(localStorage.token).catch((error) => {
			return {
				current: WEBUI_VERSION,
				latest: null
			};
		});

		console.info(version);

		updateAvailable = compareVersion(version.latest, version.current);
		console.info(updateAvailable);
	};

	const updateLdapServerHandler = async () => {
		if (!ENABLE_LDAP) return;
		const res = await updateLdapServer(localStorage.token, LDAP_SERVER).catch((error) => {
			toast.error(`${error}`);
			return null;
		});
		if (res) {
			toast.success($i18n.t('LDAP server updated'));
		}
	};

	const updateBanners = async () => {
		_banners.set(await setBanners(localStorage.token, banners));
	};

	const saveDefaultInterfaceSettings = (updated: Record<string, any>) => {
		defaultInterfaceSettings = { ...defaultInterfaceSettings, ...updated };
	};

	const getDefaultInterfaceSettings = () => {
		const value = adminConfig?.DEFAULT_INTERFACE_SETTINGS;
		return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
	};

	const updateHandler = async () => {
		if (saving) return;
		saving = true;
		try {
			// [Gradient] LDAP stays inline (Q14); save it before the admin config,
			// which may revoke sessions.
			await updateLdapConfig(localStorage.token, ENABLE_LDAP);
			await updateLdapServerHandler();

			const cleaned = entriesToI18n(uiI18nEntries);
			const res = await updateAdminConfig(localStorage.token, {
				...adminConfig,
				SKILL_CONTENT_MAX_CHARS: adminConfig.SKILL_CONTENT_MAX_CHARS ?? null,
				SKILL_PREVIEW_MAX_CHARS: adminConfig.SKILL_PREVIEW_MAX_CHARS ?? null,
				DEFAULT_INTERFACE_SETTINGS: defaultInterfaceSettings,
				I18N: cleaned
			});
			if (!res) throw new Error($i18n.t('Failed to update settings'));
			if (res.sessions_revoked) {
				localStorage.removeItem('token');
				window.location.href = '/auth?state=logout&form=signin';
				return;
			}
			await updateI18n(res.I18N ?? cleaned);
			await updateBanners();
			await config.set(await getBackendConfig());
			saveHandler();
		} catch (error) {
			toast.error(
				error instanceof Error
					? error.message
					: Array.isArray(error)
						? error.map((entry) => entry.msg).join('\n')
						: String(error)
			);
		} finally {
			saving = false;
		}
	};

	onMount(async () => {
		adminConfig = await getAdminConfig(localStorage.token);
		defaultInterfaceSettings = getDefaultInterfaceSettings();
		uiI18nEntries = i18nToEntries(adminConfig.I18N ?? {});

		groups = await getGroups(localStorage.token);

		LDAP_SERVER = await getLdapServer(localStorage.token);
		const ldapConfig = await getLdapConfig(localStorage.token);
		ENABLE_LDAP = ldapConfig.ENABLE_LDAP;

		banners = [...$_banners];

		if (
			isFeatureEnabled('changelog') &&
			WEBUI_BUILD_CHANNEL === 'main' &&
			$config?.features?.enable_version_update_check
		) {
			checkForVersionUpdates();
		}
	});
</script>

<form
	class="flex h-full flex-col justify-between text-sm"
	on:submit|preventDefault={async () => {
		updateHandler();
	}}
>
	<h2 class="text-sm font-medium text-gray-900 dark:text-white mb-4">
		{$i18n.t('settings.admin.general.title')}
	</h2>

	<div class="flex-1 min-h-0 overflow-y-auto scrollbar-hover pr-1.5">
		{#if adminConfig !== null}
			<AdminSettingSection first>
				<div class="flex items-start justify-between gap-4">
					<div class="min-w-0 text-xs">
						<div class="text-gray-600 dark:text-gray-400">
							{$i18n.t('settings.admin.general.version.label')}
						</div>
						<div class="mt-1 flex flex-wrap gap-x-1 text-gray-700 dark:text-gray-200">
							<BuildVersion />

							{#if isFeatureEnabled('changelog') && WEBUI_BUILD_CHANNEL === 'main' && $config?.features?.enable_version_update_check}
								{#if version.latest === null}
									<span class="text-gray-500 dark:text-gray-500"
										>{$i18n.t('Could not check for updates')}</span
									>
								{:else}
									<a
										href="https://github.com/open-webui/open-webui/releases/tag/v{version.latest}"
										target="_blank"
										class="text-gray-500 hover:text-gray-700 dark:text-gray-500 dark:hover:text-gray-300"
									>
										{updateAvailable === null
											? $i18n.t('Checking for updates...')
											: updateAvailable
												? `(v${version.latest} ${$i18n.t('available!')})`
												: $i18n.t('(latest)')}
									</a>
								{/if}
							{/if}
						</div>

						{#if isFeatureEnabled('changelog')}<button
								class="mt-0.5 text-xs text-gray-400 transition-colors hover:text-gray-700 dark:text-gray-600 dark:hover:text-gray-300"
								type="button"
								on:click={() => {
									showChangelog.set(true);
								}}
							>
								{$i18n.t('settings.admin.general.seeWhatSNew.label')}
							</button>{/if}
					</div>

					{#if isFeatureEnabled('changelog') && WEBUI_BUILD_CHANNEL === 'main' && $config?.features?.enable_version_update_check}
						<button
							class="shrink-0 text-xs text-gray-500 transition-colors hover:text-gray-900 dark:text-gray-500 dark:hover:text-white"
							type="button"
							on:click={() => {
								checkForVersionUpdates();
							}}
						>
							{$i18n.t('settings.admin.general.checkForUpdates.label')}
						</button>
					{/if}
				</div>

				<div class="text-xs">
					<div class="flex items-start justify-between gap-4">
						<div class="min-w-0">
							<div class="text-gray-600 dark:text-gray-400">
								{$i18n.t('settings.admin.general.help.label')}
							</div>
							<div class="mt-0.5 text-gray-400 dark:text-gray-600">
								<!-- LICENSE covers this Open WebUI wordmark.
								Do not alter, remove, obscure, or replace it except as LICENSE permits:
								https://docs.openwebui.com/license. -->
								{$i18n.t('settings.admin.general.help.description')}
							</div>
						</div>

						<a
							class="shrink-0 text-gray-500 transition-colors hover:text-gray-900 dark:text-gray-500 dark:hover:text-white"
							href="https://docs.openwebui.com/"
							target="_blank"
						>
							{$i18n.t('Documentation')}
						</a>
					</div>

					<div class="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-gray-400 dark:text-gray-600">
						<a
							class="hover:text-gray-700 dark:hover:text-gray-300"
							href="https://discord.gg/5rJgQTnV4s"
							target="_blank">Discord</a
						>
						<a
							class="hover:text-gray-700 dark:hover:text-gray-300"
							href="https://twitter.com/OpenWebUI"
							target="_blank">X</a
						>
						<a
							class="hover:text-gray-700 dark:hover:text-gray-300"
							href="https://github.com/open-webui/open-webui"
							target="_blank">GitHub</a
						>
					</div>
				</div>

				<div class="text-xs">
					<!-- LICENSE covers this Open WebUI license attribution.
					Do not alter, remove, obscure, or replace it except as LICENSE permits:
					https://docs.openwebui.com/license. -->
					<div class="text-gray-600 dark:text-gray-400">
						{$i18n.t('settings.admin.general.license.label')}
					</div>

					{#if $config?.license_metadata}
						<a
							href="https://docs.openwebui.com/enterprise"
							target="_blank"
							class="mt-0.5 block text-gray-500"
						>
							<span class="capitalize text-black dark:text-white"
								>{$config?.license_metadata?.type} license</span
							>
							{$i18n.t('registered to')}
							<span class="capitalize text-black dark:text-white"
								>{$config?.license_metadata?.organization_name}</span
							>
							for
							<span class="text-black dark:text-white"
								>{$config?.license_metadata?.seats ?? 'Unlimited'} users.</span
							>
						</a>
						{#if $config?.license_metadata?.html}
							<div class="mt-0.5 text-gray-500">
								{@html DOMPurify.sanitize($config?.license_metadata?.html)}
							</div>
						{/if}
					{:else}
						<a
							class="mt-0.5 block text-gray-400 transition-colors hover:text-gray-700 dark:text-gray-600 dark:hover:text-gray-300"
							href="https://docs.openwebui.com/enterprise"
							target="_blank"
						>
							{$i18n.t(
								'Upgrade to a licensed plan for enhanced capabilities, including custom theming and branding, and dedicated support.'
							)}
						</a>
					{/if}
				</div>
			</AdminSettingSection>

			<!-- [Gradient] Q14 keeps authentication inline; LDAP group-management controls remain hidden. -->
			<AdminSettingSection title={$i18n.t('Authentication')}>
				<AdminSettingRow
					label={$i18n.t('Default User Role')}
					description={$i18n.t('Role assigned to new users when they create an account.')}
				>
					<SettingsSelect
						bind:value={adminConfig.DEFAULT_USER_ROLE}
						placeholder={$i18n.t('Select a role')}
					>
						<option value="pending">{$i18n.t('pending')}</option>
						<option value="user">{$i18n.t('user')}</option>
						<option value="admin">{$i18n.t('admin')}</option>
					</SettingsSelect>
				</AdminSettingRow>

				<AdminSettingRow
					label={$i18n.t('Default Group')}
					description={$i18n.t('Group assigned to new users by default.')}
				>
					<SettingsSelect
						bind:value={adminConfig.DEFAULT_GROUP_ID}
						placeholder={$i18n.t('Select a group')}
					>
						<option value={''}>None</option>
						{#each groups as group}
							<option value={group.id}>{group.name}</option>
						{/each}
					</SettingsSelect>
				</AdminSettingRow>

				<AdminSettingRow
					label={$i18n.t('New Sign Ups')}
					description={$i18n.t('Allow new users to create accounts.')}
					let:labelId
				>
					<Switch bind:state={adminConfig.ENABLE_SIGNUP} ariaLabelledbyId={labelId} />
				</AdminSettingRow>

				<AdminSettingRow
					label={$i18n.t('API Keys')}
					description={$i18n.t('Allow users to create API keys for programmatic access.')}
					let:labelId
				>
					<Switch bind:state={adminConfig.ENABLE_API_KEYS} ariaLabelledbyId={labelId} />
				</AdminSettingRow>

				{#if adminConfig?.ENABLE_API_KEYS}
					<AdminSettingRow
						label={$i18n.t('API Key Endpoint Restrictions')}
						description={$i18n.t('Limit API keys to configured endpoints.')}
						let:labelId
					>
						<Switch
							bind:state={adminConfig.ENABLE_API_KEYS_ENDPOINT_RESTRICTIONS}
							ariaLabelledbyId={labelId}
						/>
					</AdminSettingRow>

					{#if adminConfig?.ENABLE_API_KEYS_ENDPOINT_RESTRICTIONS}
						<AdminSettingField
							label={$i18n.t('Allowed Endpoints')}
							description={$i18n.t('Comma-separated API paths that API keys can access.')}
						>
							<input
								class={inputClass}
								type="text"
								placeholder={`e.g.) /api/v1/messages, /api/v1/channels`}
								bind:value={adminConfig.API_KEYS_ALLOWED_ENDPOINTS}
							/>
							<a
								href="https://docs.openwebui.com/reference/api-endpoints"
								target="_blank"
								class="mt-1 block text-[0.6875rem] text-gray-400 underline hover:text-gray-700 dark:text-gray-600 dark:hover:text-gray-300"
							>
								{$i18n.t('To learn more about available endpoints, visit our documentation.')}
							</a>
						</AdminSettingField>
					{/if}
				{/if}

				<AdminSettingField
					label={$i18n.t('JWT Expiration')}
					description={$i18n.t(
						"Valid time units: 's', 'm', 'h', 'd', 'w' or '-1' for no expiration."
					)}
				>
					<input
						class={inputClass}
						type="text"
						placeholder={`e.g.) "30m","1h", "10d". `}
						bind:value={adminConfig.JWT_EXPIRES_IN}
					/>

					{#if adminConfig.JWT_EXPIRES_IN === '-1'}
						<a
							href="https://docs.openwebui.com/reference/env-configuration#jwt_expires_in"
							target="_blank"
							class="mt-1 block rounded-lg bg-yellow-500/10 px-2 py-1.5 text-[0.6875rem] text-yellow-700 underline dark:text-yellow-200"
						>
							{$i18n.t('No expiration can pose security risks.')}
						</a>
					{/if}
				</AdminSettingField>
			</AdminSettingSection>

			<AdminSettingSection title={$i18n.t('Pending Accounts')}>
				<AdminSettingRow
					label={$i18n.t('Admin Details')}
					description={$i18n.t('Show admin contact details while an account waits for approval.')}
					let:labelId
				>
					<Switch bind:state={adminConfig.SHOW_ADMIN_DETAILS} ariaLabelledbyId={labelId} />
				</AdminSettingRow>

				{#if adminConfig.SHOW_ADMIN_DETAILS}
					<AdminSettingField
						label={$i18n.t('Admin Contact Email')}
						description={$i18n.t('Email shown in the pending account overlay.')}
					>
						<input
							class={inputClass}
							type="email"
							placeholder={$i18n.t('Leave empty to use first admin user')}
							bind:value={adminConfig.ADMIN_EMAIL}
						/>
					</AdminSettingField>
				{/if}

				<AdminSettingField
					label={$i18n.t('Pending User Overlay Title')}
					description={$i18n.t('Custom title shown while an account waits for approval.')}
				>
					<Textarea
						className={textareaClass}
						placeholder={$i18n.t(
							'Enter a title for the pending user info overlay. Leave empty for default.'
						)}
						bind:value={adminConfig.PENDING_USER_OVERLAY_TITLE}
					/>
				</AdminSettingField>

				<AdminSettingField
					label={$i18n.t('Pending User Overlay Content')}
					description={$i18n.t('Custom message shown while an account waits for approval.')}
				>
					<Textarea
						className={textareaClass}
						placeholder={$i18n.t(
							'Enter content for the pending user info overlay. Leave empty for default.'
						)}
						bind:value={adminConfig.PENDING_USER_OVERLAY_CONTENT}
					/>
				</AdminSettingField>
			</AdminSettingSection>

			<AdminSettingSection title={$i18n.t('LDAP')}>
				<AdminSettingRow
					label={$i18n.t('LDAP')}
					description={$i18n.t('Allow users to authenticate with an LDAP directory.')}
					let:labelId
				>
					<Switch bind:state={ENABLE_LDAP} ariaLabelledbyId={labelId} />
				</AdminSettingRow>

				{#if ENABLE_LDAP}
					<div class="grid grid-cols-1 gap-x-3 gap-y-2.5 sm:grid-cols-2">
						<AdminSettingField
							label={$i18n.t('Label')}
							description={$i18n.t('Display name for this LDAP connection.')}
						>
							<input
								class={inputClass}
								required
								placeholder={$i18n.t('Enter server label')}
								bind:value={LDAP_SERVER.label}
							/>
						</AdminSettingField>
					</div>

					<div class="grid grid-cols-1 gap-x-3 gap-y-2.5 sm:grid-cols-2">
						<AdminSettingField
							label={$i18n.t('Host')}
							description={$i18n.t('LDAP server hostname or IP address.')}
						>
							<input
								class={inputClass}
								required
								placeholder={$i18n.t('Enter server host')}
								bind:value={LDAP_SERVER.host}
							/>
						</AdminSettingField>

						<AdminSettingField label={$i18n.t('Port')} description={$i18n.t('LDAP server port.')}>
							<Tooltip
								placement="top-start"
								content={$i18n.t('Default to 389 or 636 if TLS is enabled')}
								className="w-full"
							>
								<input
									class={inputClass}
									type="number"
									placeholder={$i18n.t('Enter server port')}
									bind:value={LDAP_SERVER.port}
								/>
							</Tooltip>
						</AdminSettingField>
					</div>

					<div class="grid grid-cols-1 gap-x-3 gap-y-2.5 sm:grid-cols-2">
						<AdminSettingField
							label={$i18n.t('Application DN')}
							description={$i18n.t('Bind DN used for directory search.')}
						>
							<Tooltip
								content={$i18n.t('The Application Account DN you bind with for search')}
								placement="top-start"
							>
								<input
									class={inputClass}
									placeholder={$i18n.t('Enter Application DN')}
									bind:value={LDAP_SERVER.app_dn}
								/>
							</Tooltip>
						</AdminSettingField>

						<AdminSettingField
							label={$i18n.t('Application DN Password')}
							description={$i18n.t('Password for the bind DN.')}
						>
							<SensitiveInput
								variant="settings"
								placeholder={$i18n.t('Enter Application DN Password')}
								required={false}
								bind:value={LDAP_SERVER.app_dn_password}
							/>
						</AdminSettingField>
					</div>

					<div class="grid grid-cols-1 gap-x-3 gap-y-2.5 sm:grid-cols-2">
						<AdminSettingField
							label={$i18n.t('Attribute for Mail')}
							description={$i18n.t('LDAP attribute used as the user email address.')}
						>
							<Tooltip
								content={$i18n.t(
									'The LDAP attribute that maps to the mail that users use to sign in.'
								)}
								placement="top-start"
							>
								<input
									class={inputClass}
									required
									placeholder={$i18n.t('Example: mail')}
									bind:value={LDAP_SERVER.attribute_for_mail}
								/>
							</Tooltip>
						</AdminSettingField>

						<AdminSettingField
							label={$i18n.t('Attribute for Username')}
							description={$i18n.t('LDAP attribute used as the username.')}
						>
							<Tooltip
								content={$i18n.t(
									'The LDAP attribute that maps to the username that users use to sign in.'
								)}
								placement="top-start"
							>
								<input
									class={inputClass}
									required
									placeholder={$i18n.t('Example: sAMAccountName or uid or userPrincipalName')}
									bind:value={LDAP_SERVER.attribute_for_username}
								/>
							</Tooltip>
						</AdminSettingField>
					</div>

					<AdminSettingField
						label={$i18n.t('Search Base')}
						description={$i18n.t('Base DN used when searching for users.')}
					>
						<Tooltip content={$i18n.t('The base to search for users')} placement="top-start">
							<input
								class={inputClass}
								required
								placeholder={$i18n.t('Example: ou=users,dc=foo,dc=example')}
								bind:value={LDAP_SERVER.search_base}
							/>
						</Tooltip>
					</AdminSettingField>

					<AdminSettingField
						label={$i18n.t('Search Filters')}
						description={$i18n.t('LDAP filter used to match signing-in users.')}
					>
						<input
							class={inputClass}
							placeholder={$i18n.t('Example: (&(objectClass=inetOrgPerson)(uid=%s))')}
							bind:value={LDAP_SERVER.search_filters}
						/>
						<a
							class="mt-1 block text-[0.6875rem] text-gray-400 underline hover:text-gray-700 dark:text-gray-600 dark:hover:text-gray-300"
							href="https://ldap.com/ldap-filters/"
							target="_blank"
						>
							{$i18n.t('Click here for filter guides.')}
						</a>
					</AdminSettingField>

					<AdminSettingRow
						label={$i18n.t('TLS')}
						description={$i18n.t('Use TLS when connecting to the LDAP server.')}
						let:labelId
					>
						<Switch bind:state={LDAP_SERVER.use_tls} ariaLabelledbyId={labelId} />
					</AdminSettingRow>

					{#if LDAP_SERVER.use_tls}
						<AdminSettingField
							label={$i18n.t('Certificate Path')}
							description={$i18n.t('Certificate file used for TLS verification.')}
						>
							<input
								class={inputClass}
								placeholder={$i18n.t('Enter certificate path')}
								bind:value={LDAP_SERVER.certificate_path}
							/>
						</AdminSettingField>

						<AdminSettingRow
							label={$i18n.t('Validate Certificate')}
							description={$i18n.t('Verify the LDAP server certificate when TLS is enabled.')}
							let:labelId
						>
							<Switch bind:state={LDAP_SERVER.validate_cert} ariaLabelledbyId={labelId} />
						</AdminSettingRow>

						<AdminSettingField
							label={$i18n.t('Ciphers')}
							description={$i18n.t('TLS cipher list for LDAP connections.')}
						>
							<Tooltip content={$i18n.t('Default to ALL')} placement="top-start">
								<input
									class={inputClass}
									placeholder={$i18n.t('Example: ALL')}
									bind:value={LDAP_SERVER.ciphers}
								/>
							</Tooltip>
						</AdminSettingField>
					{/if}

					<!-- LICENSE covers this Open WebUI wordmark.
					Do not alter, remove, obscure, or replace it except as LICENSE permits:
					https://docs.openwebui.com/license. -->
				{/if}
			</AdminSettingSection>

			<AdminSettingSection title={$i18n.t('settings.admin.general.sections.features.title')}>
				<!-- [Gradient] Fork attribution and citation-relevance control. -->
				<AdminSettingRow label={$i18n.t('Powered by soev.ai')}>
					<a
						href="https://soev.ai"
						target="_blank"
						class="inline-flex items-center gap-1.5 text-xs text-gray-400"
						><img
							src="{WEBUI_BASE_URL}/static/gradient-logo.png"
							alt="Gradient"
							class="size-6"
						/>soev.ai</a
					>
				</AdminSettingRow>
				<AdminSettingRow label={$i18n.t('Citation Relevance')} let:labelId
					><Switch
						bind:state={adminConfig.ENABLE_CITATION_RELEVANCE}
						ariaLabelledbyId={labelId}
					/></AdminSettingRow
				>
				<AdminSettingRow
					label={$i18n.t('settings.admin.general.communitySharing.label')}
					description={$i18n.t('settings.admin.general.communitySharing.description')}
					let:labelId
				>
					<!-- LICENSE covers this Open WebUI Community wordmark.
					Do not alter, remove, obscure, or replace it except as LICENSE permits:
					https://docs.openwebui.com/license. -->
					<Switch bind:state={adminConfig.ENABLE_COMMUNITY_SHARING} ariaLabelledbyId={labelId} />
				</AdminSettingRow>
				<AdminSettingRow
					label={$i18n.t('settings.admin.general.messageRating.label')}
					description={$i18n.t('settings.admin.general.messageRating.description')}
					let:labelId
				>
					<Switch bind:state={adminConfig.ENABLE_MESSAGE_RATING} ariaLabelledbyId={labelId} />
				</AdminSettingRow>
				<AdminSettingRow
					label={$i18n.t('settings.admin.general.folders.label')}
					description={$i18n.t('settings.admin.general.folders.description')}
					let:labelId
				>
					<Switch bind:state={adminConfig.ENABLE_FOLDERS} ariaLabelledbyId={labelId} />
				</AdminSettingRow>

				{#if adminConfig.ENABLE_FOLDERS}
					<AdminSettingField
						label={$i18n.t('settings.admin.general.folderMaxFileCount.label')}
						description={$i18n.t('settings.admin.general.folderMaxFileCount.description')}
					>
						<input
							class={inputClass}
							type="number"
							min="0"
							placeholder={$i18n.t('Leave empty for unlimited')}
							bind:value={adminConfig.FOLDER_MAX_FILE_COUNT}
						/>
					</AdminSettingField>
				{/if}

				<AdminSettingRow
					label={$i18n.t('settings.admin.general.memories.label')}
					description={$i18n.t('settings.admin.general.memories.description')}
					let:labelId
				>
					<Switch bind:state={adminConfig.ENABLE_MEMORIES} ariaLabelledbyId={labelId} />
				</AdminSettingRow>
				{#if adminConfig.ENABLE_MEMORIES}
					<AdminSettingRow
						label={$i18n.t('settings.admin.general.memorySystemContext.label')}
						description={$i18n.t('settings.admin.general.memorySystemContext.description')}
						labelClassName="text-gray-500 dark:text-gray-500"
						let:labelId
					>
						<Switch
							bind:state={adminConfig.ENABLE_MEMORY_SYSTEM_CONTEXT}
							ariaLabelledbyId={labelId}
						/>
					</AdminSettingRow>
				{/if}
				<AdminSettingField
					forId="skill-content-max-chars"
					label={$i18n.t('settings.admin.general.skillContentMaxChars.label')}
					description={$i18n.t('settings.admin.general.skillContentMaxChars.description')}
				>
					<input
						id="skill-content-max-chars"
						class={inputClass}
						type="number"
						min="1"
						step="1"
						placeholder={$i18n.t('Default')}
						bind:value={adminConfig.SKILL_CONTENT_MAX_CHARS}
					/>
				</AdminSettingField>
				<AdminSettingField
					forId="skill-preview-max-chars"
					label={$i18n.t('settings.admin.general.skillPreviewMaxChars.label')}
					description={$i18n.t('settings.admin.general.skillPreviewMaxChars.description')}
				>
					<input
						id="skill-preview-max-chars"
						class={inputClass}
						type="number"
						min="1"
						step="1"
						placeholder={$i18n.t('Default')}
						bind:value={adminConfig.SKILL_PREVIEW_MAX_CHARS}
					/>
				</AdminSettingField>
				<AdminSettingRow
					label={$i18n.t('settings.admin.general.notes.label')}
					description={$i18n.t('settings.admin.general.notes.description')}
					let:labelId
				>
					<Switch bind:state={adminConfig.ENABLE_NOTES} ariaLabelledbyId={labelId} />
				</AdminSettingRow>
				<AdminSettingRow
					label={$i18n.t('settings.admin.general.channels.label')}
					description={$i18n.t('settings.admin.general.channels.description')}
					let:labelId
				>
					<Switch bind:state={adminConfig.ENABLE_CHANNELS} ariaLabelledbyId={labelId} />
				</AdminSettingRow>
				{#if adminConfig.ENABLE_CHANNELS}
					<AdminSettingRow
						label={$i18n.t('settings.admin.general.encryptedChannels.label')}
						description={$i18n.t('settings.admin.general.encryptedChannels.description')}
						labelClassName="text-gray-500 dark:text-gray-500"
						let:labelId
					>
						<Switch bind:state={adminConfig.ENABLE_ENCRYPTED_CHANNELS} ariaLabelledbyId={labelId} />
					</AdminSettingRow>
					<AdminSettingRow
						label={$i18n.t('settings.admin.general.modelResponseMode.label')}
						description={$i18n.t('settings.admin.general.modelResponseMode.description')}
						labelClassName="text-gray-500 dark:text-gray-500"
						let:labelId
					>
						<SettingsSelect
							bind:value={adminConfig.CHANNEL_MODEL_RESPONSE_MODE}
							aria-labelledby={labelId}
						>
							<option value="thread">{$i18n.t('Thread')}</option>
							<option value="channel">{$i18n.t('Channel')}</option>
						</SettingsSelect>
					</AdminSettingRow>
				{/if}
				<AdminSettingRow
					label={$i18n.t('settings.admin.general.apps.label')}
					description={$i18n.t('settings.admin.general.apps.description')}
					let:labelId
				>
					<Switch bind:state={adminConfig.ENABLE_APPS} ariaLabelledbyId={labelId} />
				</AdminSettingRow>
				<AdminSettingRow
					label={$i18n.t('settings.admin.general.calendar.label')}
					description={$i18n.t('settings.admin.general.calendar.description')}
					let:labelId
				>
					<Switch bind:state={adminConfig.ENABLE_CALENDAR} ariaLabelledbyId={labelId} />
				</AdminSettingRow>
				<AdminSettingRow
					label={$i18n.t('settings.admin.general.automations.label')}
					description={$i18n.t('settings.admin.general.automations.description')}
					let:labelId
				>
					<Switch bind:state={adminConfig.ENABLE_AUTOMATIONS} ariaLabelledbyId={labelId} />
				</AdminSettingRow>
				<AdminSettingRow
					label={$i18n.t('settings.admin.general.userWebhooks.label')}
					description={$i18n.t('settings.admin.general.userWebhooks.description')}
					let:labelId
				>
					<Switch bind:state={adminConfig.ENABLE_USER_WEBHOOKS} ariaLabelledbyId={labelId} />
				</AdminSettingRow>
				<AdminSettingRow
					label={$i18n.t('settings.admin.general.userStatus.label')}
					description={$i18n.t('settings.admin.general.userStatus.description')}
					let:labelId
				>
					<Switch bind:state={adminConfig.ENABLE_USER_STATUS} ariaLabelledbyId={labelId} />
				</AdminSettingRow>

				<AdminSettingField
					label={$i18n.t('settings.admin.general.responseWatermark.label')}
					description={$i18n.t('settings.admin.general.responseWatermark.description')}
				>
					<Textarea
						className={textareaClass}
						placeholder={$i18n.t('Enter a watermark for the response. Leave empty for none.')}
						bind:value={adminConfig.RESPONSE_WATERMARK}
					/>
				</AdminSettingField>

				<AdminSettingField
					label={$i18n.t('settings.admin.general.webuiUrl.label')}
					description={$i18n.t('settings.admin.general.webuiUrl.description')}
				>
					<input
						class={inputClass}
						type="text"
						placeholder={`e.g.) "http://localhost:3000"`}
						bind:value={adminConfig.WEBUI_URL}
					/>
				</AdminSettingField>
			</AdminSettingSection>

			<Events />

			<AdminSettingSection title={$i18n.t('settings.admin.general.sections.ui.title')}>
				<fieldset id="ui-i18n-settings" disabled={saving} class="min-w-0">
					<I18nSettings bind:entries={uiI18nEntries} />
				</fieldset>
				<div class="shrink-0">
					<div class="flex items-center justify-between gap-4 py-0.5">
						<button
							class="min-w-0 flex-1 text-left text-xs text-gray-600 transition hover:text-gray-900 dark:text-gray-400 dark:hover:text-gray-100"
							type="button"
							on:click={() => {
								showUserUiDefaults = !showUserUiDefaults;
							}}
						>
							<div>{$i18n.t('Default Interface Settings')}</div>
							<div class="mt-1.5 text-[0.6875rem] text-gray-400 dark:text-gray-600">
								{$i18n.t(
									'Set system-wide interface defaults for every account. Personal settings override these defaults.'
								)}
							</div>
						</button>

						<button
							class="shrink-0 text-[0.6875rem] text-gray-400 transition hover:text-gray-700 dark:text-gray-600 dark:hover:text-gray-300"
							type="button"
							on:click={() => {
								showUserUiDefaults = !showUserUiDefaults;
							}}
						>
							{showUserUiDefaults ? $i18n.t('Close') : $i18n.t('Configure')}
						</button>
					</div>

					{#if showUserUiDefaults}
						<div class="mt-0.5 space-y-2">
							<div class="flex items-center justify-between gap-4 py-0.5">
								<div class="text-[0.6875rem] text-gray-400 dark:text-gray-600">
									{Object.keys(defaultInterfaceSettings).length}
									{$i18n.t('settings configured')}
								</div>

								{#if Object.keys(defaultInterfaceSettings).length > 0}
									<button
										class="shrink-0 text-[0.6875rem] text-gray-400 transition hover:text-gray-700 dark:text-gray-600 dark:hover:text-gray-300"
										type="button"
										on:click={() => {
											defaultInterfaceSettings = {};
										}}
									>
										{$i18n.t('Clear')}
									</button>
								{/if}
							</div>

							<div class="max-h-[28rem] overflow-y-auto pb-2 pr-1 scrollbar-hover">
								<InterfaceSettings
									settingsValue={defaultInterfaceSettings}
									saveSettings={saveDefaultInterfaceSettings}
								/>
							</div>
						</div>
					{/if}
				</div>

				<div>
					<div class="mb-1 flex min-h-7 w-full items-center justify-between gap-2">
						<div class="min-w-0 text-xs text-gray-600 dark:text-gray-400">
							{$i18n.t('settings.admin.general.banners.label')}
						</div>
						<div class="flex shrink-0 items-center gap-1">
							{#if banners.length > 0}
								<LanguageModeSelect bind:value={bannerLocale} className="w-fit" />
							{/if}
							<button
								class="flex size-6 items-center justify-center text-gray-400 dark:text-gray-600"
								type="button"
								aria-label={$i18n.t('settings.admin.general.addBanner.label')}
								on:click={() => {
									if (banners.length === 0 || banners[banners.length - 1]?.content !== '') {
										banners = [
											...banners,
											{
												id: uuidv4(),
												type: '',
												title: '',
												content: '',
												dismissible: true,
												timestamp: Math.floor(Date.now() / 1000)
											}
										];
									}
								}}
							>
								<Plus />
							</button>
						</div>
					</div>
					<div class="mb-2 text-[0.6875rem] text-gray-400 dark:text-gray-600">
						{$i18n.t('Create announcements shown to users in the app.')}
					</div>
					<Banners bind:banners locale={bannerLocale} />
				</div>
			</AdminSettingSection>
		{/if}
	</div>

	<div class="flex justify-end pt-6 text-sm font-normal">
		<button
			class="flex h-7 shrink-0 items-center justify-center gap-1.5 rounded-lg bg-gray-900 px-2.5 text-xs font-normal text-white transition hover:bg-black disabled:opacity-60 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white"
			type="submit"
			disabled={saving}
		>
			{$i18n.t('Save')}
		</button>
	</div>
</form>
