<script lang="ts">
	import { onMount, onDestroy, tick, getContext } from 'svelte';
	import { toast } from 'svelte-sonner';
	import Sortable from 'sortablejs';

	import {
		detectAgents,
		createAgentConfig,
		updateAgentConfig,
		deleteAgentConfig,
		reorderAgentConfigs,
		type AgentConfigDetectionRow,
		type AgentConfigForm,
		type AgentAccessGrant
	} from '$lib/apis/agent-configs';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import Switch from '$lib/components/common/Switch.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import EllipsisVertical from '$lib/components/icons/EllipsisVertical.svelte';
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

	// SortableJS instance + the DOM node it's attached to.
	let configuredListElement: HTMLElement | null = null;
	let sortable: Sortable | null = null;
	let reordering = false;
	// True while a drag is in flight — collapses every card to a
	// compact row so the floating clone doesn't overlay the full-height
	// form fields of the cards underneath it.
	let dragging = false;

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

	const persistOrder = async (slugs: string[]) => {
		reordering = true;
		try {
			await reorderAgentConfigs(localStorage.token, slugs);
			await refresh();
			saveHandler();
		} catch (err: any) {
			toast.error(err?.detail ?? $i18n.t('Failed to reorder agents'));
			// Backend rejected — pull authoritative state back.
			await refresh();
		} finally {
			reordering = false;
		}
	};

	const handleSortUpdate = (event: any) => {
		const { oldIndex, newIndex, item } = event;
		if (oldIndex === undefined || newIndex === undefined || oldIndex === newIndex) {
			return;
		}
		// Revert SortableJS's DOM mutation so Svelte's reactive {#each} stays
		// in sync with virtual DOM (mirrors the ModelList.svelte pattern).
		const parent = item.parentNode as HTMLElement | null;
		if (parent) {
			const target = parent.children[oldIndex < newIndex ? oldIndex : oldIndex + 1] ?? null;
			parent.insertBefore(item, target);
		}
		const slugs = configured.map((r) => r.slug);
		const [moved] = slugs.splice(oldIndex, 1);
		slugs.splice(newIndex, 0, moved);
		// Optimistic local reorder so the row visibly settles in the new slot
		// before the network round-trip resolves.
		rows = [...slugs.map((s) => configured.find((r) => r.slug === s)!), ...unconfigured];
		void persistOrder(slugs);
	};

	const initSortable = () => {
		if (sortable) {
			sortable.destroy();
			sortable = null;
		}
		if (configuredListElement) {
			sortable = new Sortable(configuredListElement, {
				animation: 150,
				handle: '.agent-item-handle',
				// Use the JS fallback drag clone instead of the browser's
				// native HTML5 drag image. The native drag image is a
				// static bitmap and ignores CSS — fallback gives us a
				// real DOM node with class ``.sortable-drag`` that we
				// can style as the compact summary view.
				forceFallback: true,
				fallbackOnBody: true,
				onStart: () => {
					dragging = true;
				},
				onEnd: () => {
					dragging = false;
				},
				onUpdate: handleSortUpdate
			});
		}
	};

	onMount(refresh);

	onDestroy(() => {
		if (sortable) {
			sortable.destroy();
			sortable = null;
		}
	});

	$: configured = rows.filter((r) => r.configured);
	$: unconfigured = rows.filter((r) => !r.configured && r.in_env);
	$: if (loaded && configuredListElement) {
		tick().then(() => initSortable());
	}
</script>

<div class="flex flex-col h-full text-sm">
	<div class="overflow-y-scroll scrollbar-hidden h-full">
		<div class="mt-0.5 mb-2.5 text-base font-medium">{$i18n.t('AI-agents')}</div>

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
					<div
						class="flex flex-col gap-3 {dragging ? 'agents-dragging' : ''}"
						bind:this={configuredListElement}
					>
						{#each configured as row (row.slug)}
							<div
								class="agent-card rounded-lg border border-gray-100 dark:border-gray-850 {reordering
									? 'opacity-70'
									: ''}"
								id="agent-item-{row.slug}"
							>
								<!--
									Compact summary — visible only while the parent has the
									``agents-dragging`` class (Svelte-driven) OR when the card is the
									SortableJS-cloned drag image (``.sortable-drag``). Rendering
									it permanently means the clone snapshot already has it, so the
									floating ghost shows the compact form too.
								-->
								<div class="agent-card-compact flex items-center gap-3 p-2">
									<div
										class="agent-item-handle cursor-move text-gray-400 dark:text-gray-600 shrink-0"
									>
										<EllipsisVertical className="size-4" />
									</div>
									<div class="flex items-center gap-2 min-w-0 flex-1">
										<span class="text-sm font-medium truncate">
											{drafts[row.slug]?.name || row.slug}
										</span>
										<code class="text-[11px] text-gray-500 truncate">{row.slug}</code>
										{#if !row.in_env}
											<span
												class="px-1.5 py-0.5 rounded bg-yellow-50 dark:bg-yellow-900/20 text-yellow-800 dark:text-yellow-200 text-[10px] shrink-0"
											>
												{$i18n.t('Not in AGENT_API_AGENTS')}
											</span>
										{/if}
									</div>
								</div>

								<div class="agent-card-full p-4">
									{#if !row.in_env}
										<div
											class="mb-2 px-2.5 py-1 rounded-md bg-yellow-50 dark:bg-yellow-900/20 text-yellow-800 dark:text-yellow-200 text-xs inline-block"
										>
											{$i18n.t('Not in AGENT_API_AGENTS')}
										</div>
									{/if}

									<div class="flex items-start justify-between gap-3">
										<Tooltip content={$i18n.t('Drag to reorder')}>
											<div
												class="agent-item-handle cursor-move text-gray-400 dark:text-gray-600 mt-1.5"
											>
												<EllipsisVertical className="size-4" />
											</div>
										</Tooltip>

										<div class="flex-1 min-w-0">
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
												{#if row.in_env}
													<Switch
														state={drafts[row.slug].is_active}
														on:change={(e) => toggleActive(row.slug, e.detail)}
													/>
												{:else}
													<Tooltip
														content={$i18n.t(
															'Not in AGENT_API_AGENTS — re-add the slug to your environment to activate.'
														)}
													>
														<div class="pointer-events-none opacity-50">
															<Switch state={drafts[row.slug].is_active} />
														</div>
													</Tooltip>
												{/if}
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
											{#if !row.in_env}
												<button
													type="button"
													class="px-3 py-1 text-xs rounded-full text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 transition"
													on:click={() => remove(row.slug)}
												>
													{pendingDeleteSlug === row.slug
														? $i18n.t('Click again to confirm')
														: $i18n.t('Delete agent')}
												</button>
											{/if}
										</div>
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

<style>
	/* Default: show the full editor, hide the compact summary. */
	.agent-card :global(.agent-card-compact) {
		display: none;
	}

	/* While a drag is in flight (Svelte sets this on the list container)
	   collapse every source card to its compact summary. */
	:global(.agents-dragging) .agent-card :global(.agent-card-full) {
		display: none;
	}
	:global(.agents-dragging) .agent-card :global(.agent-card-compact) {
		display: flex;
	}

	/* SortableJS clones the dragged element BEFORE Svelte's reactive
	   update has run, so the snapshot still has the full editor visible.
	   Force the clone to use the compact summary too — the same DOM
	   contains both views, switched purely with CSS. */
	:global(.sortable-drag) :global(.agent-card-full) {
		display: none !important;
	}
	:global(.sortable-drag) :global(.agent-card-compact) {
		display: flex !important;
	}

	/* The placeholder slot left behind by the dragged card — keep it as
	   a slim row so the list doesn't visually collapse beneath. */
	:global(.sortable-ghost) :global(.agent-card-full) {
		display: none !important;
	}
	:global(.sortable-ghost) :global(.agent-card-compact) {
		display: flex !important;
		opacity: 0.4;
	}
</style>
