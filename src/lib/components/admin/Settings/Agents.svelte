<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import { toast } from 'svelte-sonner';

	import {
		detectAgents,
		createAgentConfig,
		updateAgentConfig,
		deleteAgentConfig,
		type AgentConfigDetectionRow,
		type AgentConfigForm,
		type AgentAccessGrant
	} from '$lib/apis/agent-configs';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import Switch from '$lib/components/common/Switch.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import AccessControlModal from '$lib/components/workspace/common/AccessControlModal.svelte';

	const i18n = getContext('i18n');

	export let saveHandler: () => void = () => {};

	let loaded = false;
	let rows: AgentConfigDetectionRow[] = [];

	// In-memory edit state per slug — mirrors the row.config so the user can
	// edit before pressing Save.
	let drafts: Record<string, AgentConfigForm> = {};
	let savingSlug: string | null = null;
	let pendingDeleteSlug: string | null = null;

	// Access-control modal state
	let accessModalOpen = false;
	let accessModalSlug: string | null = null;
	let accessModalGrants: AgentAccessGrant[] = [];

	const blankForm = (slug: string): AgentConfigForm => ({
		name: slug,
		description: '',
		profile_image_url: null,
		cta_copy: '',
		is_active: false,
		is_beta: true,
		access_grants: []
	});

	const fromConfig = (cfg: NonNullable<AgentConfigDetectionRow['config']>): AgentConfigForm => ({
		name: cfg.name,
		description: cfg.description ?? '',
		profile_image_url: cfg.profile_image_url ?? null,
		cta_copy: cfg.cta_copy ?? '',
		is_active: cfg.is_active,
		is_beta: cfg.is_beta,
		access_grants: cfg.access_grants ?? []
	});

	const refresh = async () => {
		try {
			rows = await detectAgents(localStorage.token);
			drafts = Object.fromEntries(
				rows.map((r) => [
					r.slug,
					r.configured && r.config ? fromConfig(r.config) : blankForm(r.slug)
				])
			);
		} catch (err) {
			toast.error($i18n.t('Failed to load agents configuration'));
		} finally {
			loaded = true;
		}
	};

	const configure = async (slug: string) => {
		const form = drafts[slug] ?? blankForm(slug);
		savingSlug = slug;
		try {
			await createAgentConfig(localStorage.token, slug, form);
			toast.success($i18n.t('Agent configured'));
			await refresh();
			saveHandler();
		} catch (err: any) {
			toast.error(err?.detail ?? $i18n.t('Failed to configure agent'));
		} finally {
			savingSlug = null;
		}
	};

	const save = async (slug: string) => {
		const form = drafts[slug];
		if (!form) return;
		savingSlug = slug;
		try {
			await updateAgentConfig(localStorage.token, slug, form);
			toast.success($i18n.t('Agent updated'));
			await refresh();
			saveHandler();
		} catch (err: any) {
			toast.error(err?.detail ?? $i18n.t('Failed to save agent'));
		} finally {
			savingSlug = null;
		}
	};

	const toggleActive = async (slug: string, value: boolean) => {
		drafts[slug].is_active = value;
		await save(slug);
	};

	const toggleBeta = async (slug: string, value: boolean) => {
		drafts[slug].is_beta = value;
		await save(slug);
	};

	const remove = async (slug: string) => {
		if (pendingDeleteSlug !== slug) {
			pendingDeleteSlug = slug;
			setTimeout(() => {
				if (pendingDeleteSlug === slug) pendingDeleteSlug = null;
			}, 4000);
			return;
		}
		try {
			await deleteAgentConfig(localStorage.token, slug);
			toast.success($i18n.t('Agent deleted'));
			pendingDeleteSlug = null;
			await refresh();
			saveHandler();
		} catch (err: any) {
			toast.error(err?.detail ?? $i18n.t('Failed to delete agent'));
		}
	};

	const openAccessModal = (slug: string) => {
		accessModalSlug = slug;
		accessModalGrants = [...(drafts[slug]?.access_grants ?? [])];
		accessModalOpen = true;
	};

	const handleAccessChange = async () => {
		if (!accessModalSlug) return;
		const slug = accessModalSlug;
		drafts[slug].access_grants = accessModalGrants;
		await save(slug);
	};

	onMount(refresh);

	$: configured = rows.filter((r) => r.configured);
	$: unconfigured = rows.filter((r) => !r.configured && r.in_env);
</script>

<div class="flex flex-col h-full text-sm">
	<div class="overflow-y-scroll scrollbar-hidden h-full">
		<div class="mt-0.5 mb-2.5 text-base font-medium">{$i18n.t('Agents')}</div>

		<div class="text-xs text-gray-500 mb-3">
			{$i18n.t(
				'Configure the per-chat agent picker. Each entry maps to a slug from AGENT_API_AGENTS.'
			)}
		</div>

		{#if !loaded}
			<div class="flex h-full justify-center">
				<div class="my-auto">
					<Spinner className="size-6" />
				</div>
			</div>
		{:else}
			{#if unconfigured.length > 0}
				<div class="mb-5">
					<div class="text-xs uppercase tracking-wide text-gray-500 mb-2">
						{$i18n.t('Detected (unconfigured)')}
					</div>
					<div class="flex flex-col gap-2">
						{#each unconfigured as row (row.slug)}
							<div
								class="flex items-center justify-between p-3 rounded-lg border border-dashed border-gray-200 dark:border-gray-800"
							>
								<div class="flex flex-col">
									<span class="font-medium">{row.slug}</span>
									<span class="text-xs text-gray-500">{$i18n.t('In AGENT_API_AGENTS')}</span>
								</div>
								<button
									type="button"
									class="px-3 py-1 text-xs rounded-full bg-gray-100 hover:bg-gray-200 dark:bg-gray-850 dark:hover:bg-gray-800 transition disabled:opacity-50"
									disabled={savingSlug === row.slug}
									on:click={() => configure(row.slug)}
								>
									{savingSlug === row.slug ? $i18n.t('Configuring...') : $i18n.t('Configure agent')}
								</button>
							</div>
						{/each}
					</div>
				</div>
			{/if}

			{#if configured.length > 0}
				<div class="mb-5">
					<div class="text-xs uppercase tracking-wide text-gray-500 mb-2">
						{$i18n.t('Configured agents')}
					</div>
					<div class="flex flex-col gap-3">
						{#each configured as row (row.slug)}
							<div class="p-4 rounded-lg border border-gray-100 dark:border-gray-850">
								{#if !row.in_env}
									<div
										class="mb-2 px-2.5 py-1 rounded-md bg-yellow-50 dark:bg-yellow-900/20 text-yellow-800 dark:text-yellow-200 text-xs inline-block"
									>
										{$i18n.t('Not in AGENT_API_AGENTS')}
									</div>
								{/if}

								<div class="flex items-start justify-between gap-3">
									<div class="flex-1">
										<label class="text-xs text-gray-500" for={`name-${row.slug}`}>
											{$i18n.t('Display name')}
										</label>
										<input
											id={`name-${row.slug}`}
											class="w-full rounded-lg text-sm bg-transparent outline-hidden border border-gray-100 dark:border-gray-850 px-3 py-1.5 mb-2"
											type="text"
											bind:value={drafts[row.slug].name}
										/>

										<label class="text-xs text-gray-500" for={`desc-${row.slug}`}>
											{$i18n.t('Description')}
										</label>
										<input
											id={`desc-${row.slug}`}
											class="w-full rounded-lg text-sm bg-transparent outline-hidden border border-gray-100 dark:border-gray-850 px-3 py-1.5 mb-2"
											type="text"
											bind:value={drafts[row.slug].description}
										/>

										<label class="text-xs text-gray-500" for={`cta-${row.slug}`}>
											{$i18n.t('Card CTA copy')}
										</label>
										<textarea
											id={`cta-${row.slug}`}
											class="w-full rounded-lg text-sm bg-transparent outline-hidden border border-gray-100 dark:border-gray-850 px-3 py-1.5 mb-2"
											rows="2"
											bind:value={drafts[row.slug].cta_copy}
										></textarea>

										<label class="text-xs text-gray-500" for={`img-${row.slug}`}>
											{$i18n.t('Profile image URL')}
										</label>
										<input
											id={`img-${row.slug}`}
											class="w-full rounded-lg text-sm bg-transparent outline-hidden border border-gray-100 dark:border-gray-850 px-3 py-1.5 mb-2"
											type="url"
											placeholder="https://..."
											bind:value={drafts[row.slug].profile_image_url}
										/>

										<div class="text-[11px] text-gray-500">
											{$i18n.t('Slug:')} <code>{row.slug}</code>
										</div>
									</div>

									<div class="flex flex-col gap-2 min-w-[160px]">
										<div class="flex items-center justify-between">
											<span class="text-xs">{$i18n.t('Active')}</span>
											<Switch
												state={drafts[row.slug].is_active}
												on:change={(e) => toggleActive(row.slug, e.detail)}
											/>
										</div>
										<div class="flex items-center justify-between">
											<Tooltip
												content={$i18n.t('Show a Beta pill on the card and chat header')}
											>
												<span class="text-xs">{$i18n.t('Mark as Beta')}</span>
											</Tooltip>
											<Switch
												state={drafts[row.slug].is_beta}
												on:change={(e) => toggleBeta(row.slug, e.detail)}
											/>
										</div>
										<button
											type="button"
											class="mt-2 px-3 py-1 text-xs rounded-full bg-gray-100 hover:bg-gray-200 dark:bg-gray-850 dark:hover:bg-gray-800 transition"
											on:click={() => openAccessModal(row.slug)}
										>
											{$i18n.t('Manage access')}
										</button>
										<button
											type="button"
											class="px-3 py-1 text-xs rounded-full bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition disabled:opacity-50"
											disabled={savingSlug === row.slug}
											on:click={() => save(row.slug)}
										>
											{savingSlug === row.slug ? $i18n.t('Saving...') : $i18n.t('Save')}
										</button>
										<button
											type="button"
											class="px-3 py-1 text-xs rounded-full text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 transition"
											on:click={() => remove(row.slug)}
										>
											{pendingDeleteSlug === row.slug
												? $i18n.t('Click again to confirm')
												: $i18n.t('Delete agent')}
										</button>
									</div>
								</div>
							</div>
						{/each}
					</div>
				</div>
			{/if}

			{#if rows.length === 0}
				<div
					class="p-3 rounded-lg bg-gray-50 dark:bg-gray-850 text-gray-700 dark:text-gray-300 text-xs"
				>
					{$i18n.t('No external agents configured. Set AGENT_API_AGENTS in your environment.')}
				</div>
			{/if}
		{/if}
	</div>
</div>

<AccessControlModal
	bind:show={accessModalOpen}
	bind:accessGrants={accessModalGrants}
	onChange={handleAccessChange}
	share={true}
	sharePublic={true}
	shareUsers={true}
	accessRoles={['read']}
/>
