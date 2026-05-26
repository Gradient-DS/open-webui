<script lang="ts">
	import { onMount, getContext, createEventDispatcher } from 'svelte';
	import { toast } from 'svelte-sonner';

	import Modal from '$lib/components/common/Modal.svelte';
	import Checkbox from '$lib/components/common/Checkbox.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import ChevronRight from '$lib/components/icons/ChevronRight.svelte';
	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import Confluence from '$lib/components/icons/Confluence.svelte';

	import {
		listSites,
		listSpaces,
		listPages,
		type SyncItem,
		type ConfluenceSite,
		type ConfluenceSpaceSummary,
		type ConfluencePageSummary
	} from '$lib/apis/confluence';

	const i18n = getContext<any>('i18n');
	const dispatch = createEventDispatcher<{ select: { items: SyncItem[] } }>();

	export let show = false;
	// Reusable from the admin Cloud Sync shared-KB picker. The defaults
	// preserve the per-user picker behaviour (KnowledgeBase invocations); the
	// admin caller overrides them to retitle the modal, relabel the confirm
	// button, and pre-fill the selection from items already opted into the
	// shared KB so re-provisioning starts with the current set ticked.
	export let title: string | null = null;
	export let confirmLabel: string | null = null;
	export let currentItems: SyncItem[] = [];
	// Chat-attach mode: hide space checkboxes so users can only pick
	// individual pages (the chat handler can't aggregate descendants).
	export let pagesOnly = false;

	// ─── state ────────────────────────────────────────────────────────
	let loading = false;
	let sites: ConfluenceSite[] = [];
	let activeSite: ConfluenceSite | null = null;
	let siteUrl: string = '';

	type SpaceNode = {
		space: ConfluenceSpaceSummary;
		expanded: boolean;
		loaded: boolean;
		loadingChildren: boolean;
		children: PageNode[];
	};
	type PageNode = {
		page: ConfluencePageSummary;
		expanded: boolean;
		loaded: boolean;
		loadingChildren: boolean;
		children: PageNode[];
	};

	let spaceNodes: SpaceNode[] = [];

	// Selection keys:
	//   space:{id}      → whole space (all descendants)
	//   page:{id}       → this page + all descendants
	type SelectionKind = 'space' | 'page';
	type SelectionEntry = {
		kind: SelectionKind;
		item: SyncItem;
	};

	let selection: Map<string, SelectionEntry> = new Map();

	$: selectedCount = selection.size;

	// ─── boot ─────────────────────────────────────────────────────────
	async function bootstrap() {
		loading = true;
		try {
			const res = await listSites(localStorage.token);
			sites = res.sites ?? [];
			if (sites.length === 0) {
				toast.error($i18n.t('No Confluence sites are accessible for this account.'));
				return;
			}
			if (sites.length === 1) {
				await selectSite(sites[0]);
			}
		} catch (e) {
			toast.error(
				$i18n.t('Failed to load Confluence sites: ') +
					(e instanceof Error ? e.message : String(e))
			);
		} finally {
			loading = false;
		}
	}

	async function selectSite(site: ConfluenceSite) {
		activeSite = site;
		siteUrl = site.url;
		spaceNodes = [];
		selection = new Map();
		await loadSpaces();
		seedSelectionFromCurrent();
	}

	// Pre-tick items that are already opted into the KB so re-provisioning
	// starts with the existing selection visible. Items from other sites are
	// ignored — only this site's selection is shown while it's active.
	function seedSelectionFromCurrent() {
		if (!currentItems || currentItems.length === 0 || !activeSite) return;
		const seeded = new Map<string, SelectionEntry>();
		for (const item of currentItems) {
			if (item.cloud_id !== activeSite.cloud_id) continue;
			const key =
				item.type === 'space' ? `space:${item.item_id}` : `page:${item.item_id}`;
			seeded.set(key, { kind: item.type === 'space' ? 'space' : 'page', item });
		}
		selection = seeded;
	}

	async function loadSpaces() {
		loading = true;
		try {
			let cursor: string | null = null;
			let pages = 0;
			const collected: ConfluenceSpaceSummary[] = [];
			do {
				const res = await listSpaces(localStorage.token, activeSite!.cloud_id, cursor ?? undefined);
				collected.push(...res.spaces);
				siteUrl = res.site_url ?? siteUrl;
				cursor = res.next_cursor;
				pages++;
				if (pages > 20) break; // safety cap
			} while (cursor);

			spaceNodes = collected
				.filter((s) => s.status !== 'archived')
				.map((space) => ({
					space,
					expanded: false,
					loaded: false,
					loadingChildren: false,
					children: []
				}));
		} catch (e) {
			toast.error(
				$i18n.t('Failed to load Confluence spaces: ') +
					(e instanceof Error ? e.message : String(e))
			);
		} finally {
			loading = false;
		}
	}

	async function toggleSpaceExpand(node: SpaceNode) {
		node.expanded = !node.expanded;
		if (node.expanded && !node.loaded) {
			node.loadingChildren = true;
			spaceNodes = spaceNodes;
			try {
				const pages = await fetchAllRootPages(node.space.id);
				node.children = pages.map(toPageNode);
				node.loaded = true;
				// Lookahead — probe each root page so chevrons render correctly
				// from the start instead of needing a click to discover leaves.
				await Promise.all(node.children.map(probeChildren));
			} catch (e) {
				toast.error(
					$i18n.t('Failed to load pages: ') + (e instanceof Error ? e.message : String(e))
				);
			} finally {
				node.loadingChildren = false;
				spaceNodes = spaceNodes;
			}
		} else {
			spaceNodes = spaceNodes;
		}
	}

	async function probeChildren(node: PageNode): Promise<void> {
		if (node.loaded) return;
		try {
			const pages = await fetchAllPageChildren(node.page.id);
			node.children = pages.map(toPageNode);
			node.loaded = true;
		} catch {
			// Leave unloaded — chevron stays visible; user can retry on click.
		}
	}

	async function togglePageExpand(parent: PageNode) {
		parent.expanded = !parent.expanded;
		if (!parent.expanded) {
			spaceNodes = spaceNodes;
			return;
		}
		if (!parent.loaded) {
			// Fallback path — parent wasn't pre-probed (e.g. probe failed).
			parent.loadingChildren = true;
			spaceNodes = spaceNodes;
			try {
				await probeChildren(parent);
			} finally {
				parent.loadingChildren = false;
				spaceNodes = spaceNodes;
			}
			if (parent.children.length === 0) {
				parent.expanded = false;
				spaceNodes = spaceNodes;
				return;
			}
		}
		// Lookahead — probe one level deeper so grandchild chevrons settle.
		// Fire-and-forget: the children are already shown.
		const unprobed = parent.children.filter((c) => !c.loaded);
		if (unprobed.length > 0) {
			Promise.all(unprobed.map(probeChildren)).then(() => {
				spaceNodes = spaceNodes;
			});
		} else {
			spaceNodes = spaceNodes;
		}
	}

	async function fetchAllRootPages(spaceId: string): Promise<ConfluencePageSummary[]> {
		const collected: ConfluencePageSummary[] = [];
		let cursor: string | null = null;
		let pages = 0;
		do {
			const res = await listPages(localStorage.token, activeSite!.cloud_id, {
				spaceId,
				cursor: cursor ?? undefined
			});
			collected.push(...res.pages);
			cursor = res.next_cursor;
			pages++;
			if (pages > 20) break;
		} while (cursor);
		// Root-only: parent_id falsy
		return collected.filter((p) => !p.parent_id);
	}

	async function fetchAllPageChildren(parentId: string): Promise<ConfluencePageSummary[]> {
		const collected: ConfluencePageSummary[] = [];
		let cursor: string | null = null;
		let pages = 0;
		do {
			const res = await listPages(localStorage.token, activeSite!.cloud_id, {
				parentId,
				cursor: cursor ?? undefined
			});
			collected.push(...res.pages);
			cursor = res.next_cursor;
			pages++;
			if (pages > 20) break;
		} while (cursor);
		return collected;
	}

	function toPageNode(page: ConfluencePageSummary): PageNode {
		return {
			page,
			expanded: false,
			loaded: false,
			loadingChildren: false,
			children: []
		};
	}

	// ─── selection ────────────────────────────────────────────────────
	function keyForSpace(space: ConfluenceSpaceSummary): string {
		return `space:${space.id}`;
	}
	function keyForPage(pageId: string): string {
		return `page:${pageId}`;
	}

	function clearPageDescendants(node: PageNode) {
		for (const child of node.children) {
			selection.delete(keyForPage(child.page.id));
			clearPageDescendants(child);
		}
	}

	function clearSpaceDescendants(node: SpaceNode) {
		for (const child of node.children) {
			selection.delete(keyForPage(child.page.id));
			clearPageDescendants(child);
		}
	}

	function toggleSpaceSelection(node: SpaceNode) {
		const key = keyForSpace(node.space);
		if (selection.has(key)) {
			selection.delete(key);
		} else {
			selection.set(key, {
				kind: 'space',
				item: {
					type: 'space',
					cloud_id: activeSite!.cloud_id,
					space_id: node.space.id,
					space_key: node.space.key,
					site_url: siteUrl,
					item_id: node.space.id,
					item_path: `${activeSite!.name} / ${node.space.name}`,
					name: node.space.name,
					include_descendants: true
				}
			});
		}
		// Either direction: descendant entries are now redundant (selecting) or
		// orphaned (deselecting). Drop them so visual + sync state stay coherent.
		clearSpaceDescendants(node);
		selection = new Map(selection);
	}

	function togglePageSelection(spaceNode: SpaceNode, pageNode: PageNode, breadcrumb: string) {
		const key = keyForPage(pageNode.page.id);
		if (selection.has(key)) {
			selection.delete(key);
		} else {
			selection.set(key, {
				kind: 'page',
				item: {
					type: 'page',
					cloud_id: activeSite!.cloud_id,
					space_id: spaceNode.space.id,
					space_key: spaceNode.space.key,
					site_url: siteUrl,
					item_id: pageNode.page.id,
					item_path: breadcrumb,
					name: pageNode.page.title,
					include_descendants: true
				}
			});
		}
		clearPageDescendants(pageNode);
		selection = new Map(selection);
	}

	function pageTreeBreadcrumb(
		spaceNode: SpaceNode,
		trail: string[],
		pageNode: PageNode
	): string {
		const base = `${activeSite!.name} / ${spaceNode.space.name}`;
		if (trail.length) return `${base} / ${trail.join(' / ')} / ${pageNode.page.title}`;
		return `${base} / ${pageNode.page.title}`;
	}

	function confirmSelection() {
		if (selection.size === 0) return;
		const items: SyncItem[] = Array.from(selection.values()).map((e) => e.item);
		dispatch('select', { items });
		show = false;
	}

	onMount(() => {
		if (show) bootstrap();
	});

	$: if (show && !loading && sites.length === 0 && !activeSite) {
		bootstrap();
	}
</script>

<Modal bind:show size="lg">
	<div class="flex flex-col h-[80vh]">
		<div class="flex items-center gap-2 px-4 pt-4 pb-3">
			<Confluence className="size-5" />
			<div class="font-medium text-base">
				{title ?? (pagesOnly ? $i18n.t('Select Confluence pages') : $i18n.t('Select Confluence spaces or pages'))}
			</div>
			<button
				class="ml-auto text-sm text-gray-400 hover:text-gray-600 dark:hover:text-gray-200"
				on:click={() => (show = false)}
				aria-label={$i18n.t('Close')}
			>
				✕
			</button>
		</div>

		{#if sites.length > 1}
			<div class="px-4 pb-2 flex items-center gap-2 text-xs">
				<span class="text-gray-500">{$i18n.t('Site')}:</span>
				<select
					class="bg-gray-50 dark:bg-gray-850 text-sm rounded-lg px-2 py-1 outline-hidden"
					value={activeSite?.cloud_id ?? ''}
					on:change={(e) => {
						const next = sites.find((s) => s.cloud_id === (e.currentTarget as HTMLSelectElement).value);
						if (next) selectSite(next);
					}}
				>
					{#each sites as s}
						<option value={s.cloud_id}>{s.name}</option>
					{/each}
				</select>
			</div>
		{/if}

		<div class="flex-1 overflow-y-auto px-4 pb-2 min-h-0">
			{#if loading}
				<div class="flex items-center justify-center h-full">
					<Spinner className="size-5" />
				</div>
			{:else if !activeSite}
				<div class="flex flex-col items-center justify-center h-full gap-3 text-sm text-gray-500">
					{#each sites as s}
						<button
							class="px-3 py-1.5 rounded-lg bg-gray-50 dark:bg-gray-850 hover:bg-gray-100 dark:hover:bg-gray-800"
							on:click={() => selectSite(s)}
						>
							{s.name}
						</button>
					{/each}
				</div>
			{:else if spaceNodes.length === 0}
				<div class="flex items-center justify-center h-full text-sm text-gray-500">
					{$i18n.t('No spaces found.')}
				</div>
			{:else}
				<div class="flex flex-col gap-0.5">
					{#each spaceNodes as spaceNode}
						<div class="flex items-center gap-2 py-1">
							<button
								class="p-0.5 rounded hover:bg-gray-100 dark:hover:bg-gray-800"
								on:click={() => toggleSpaceExpand(spaceNode)}
								aria-label={spaceNode.expanded ? $i18n.t('Collapse') : $i18n.t('Expand')}
							>
								{#if spaceNode.expanded}
									<ChevronDown className="size-3.5" />
								{:else}
									<ChevronRight className="size-3.5" />
								{/if}
							</button>
							{#if pagesOnly}
								<span class="inline-block size-[18px]"></span>
							{:else}
								<Checkbox
									state={selection.has(keyForSpace(spaceNode.space)) ? 'checked' : 'unchecked'}
									on:change={() => toggleSpaceSelection(spaceNode)}
								/>
							{/if}
							<span class="text-sm font-medium">{spaceNode.space.name}</span>
							<span class="text-xs text-gray-400">({spaceNode.space.key})</span>
						</div>

						{#if spaceNode.expanded}
							<div class="ml-7">
								{#if spaceNode.loadingChildren}
									<div class="py-2 pl-1"><Spinner className="size-4" /></div>
								{:else if spaceNode.children.length === 0}
									<div class="py-1 pl-1 text-xs text-gray-400">
										{$i18n.t('No top-level pages.')}
									</div>
								{:else}
									{#each spaceNode.children as pageNode}
										{@const breadcrumbSingle = pageTreeBreadcrumb(spaceNode, [], pageNode)}
										{@const spaceCovers = selection.has(keyForSpace(spaceNode.space))}
										<div class="flex items-center gap-2 py-1">
											{#if pageNode.loaded && pageNode.children.length === 0}
												<span class="inline-block size-[18px]"></span>
											{:else}
												<button
													class="p-0.5 rounded hover:bg-gray-100 dark:hover:bg-gray-800"
													on:click={() => togglePageExpand(pageNode)}
													aria-label={pageNode.expanded ? $i18n.t('Collapse') : $i18n.t('Expand')}
												>
													{#if pageNode.expanded}
														<ChevronDown className="size-3.5" />
													{:else}
														<ChevronRight className="size-3.5" />
													{/if}
												</button>
											{/if}
											<Checkbox
												state={spaceCovers || selection.has(keyForPage(pageNode.page.id))
													? 'checked'
													: 'unchecked'}
												disabled={spaceCovers}
												on:change={() =>
													togglePageSelection(spaceNode, pageNode, breadcrumbSingle)}
											/>
											<span class="text-sm">{pageNode.page.title}</span>
										</div>

										{#if pageNode.expanded}
											<div class="ml-7">
												{#if pageNode.loadingChildren}
													<div class="py-2 pl-1"><Spinner className="size-4" /></div>
												{:else if pageNode.children.length === 0}
													<div class="py-1 pl-1 text-xs text-gray-400">
														{$i18n.t('No children.')}
													</div>
												{:else}
													{#each pageNode.children as grandChild}
														{@const grandBreadcrumb = pageTreeBreadcrumb(
															spaceNode,
															[pageNode.page.title],
															grandChild
														)}
														{@const ancestorCovers =
															selection.has(keyForSpace(spaceNode.space)) ||
															selection.has(keyForPage(pageNode.page.id))}
														<div class="flex items-center gap-2 py-1">
															{#if grandChild.loaded && grandChild.children.length === 0}
																<span class="inline-block size-[18px]"></span>
															{:else}
																<button
																	class="p-0.5 rounded hover:bg-gray-100 dark:hover:bg-gray-800"
																	on:click={() => togglePageExpand(grandChild)}
																	aria-label={grandChild.expanded
																		? $i18n.t('Collapse')
																		: $i18n.t('Expand')}
																>
																	{#if grandChild.expanded}
																		<ChevronDown className="size-3.5" />
																	{:else}
																		<ChevronRight className="size-3.5" />
																	{/if}
																</button>
															{/if}
															<Checkbox
																state={ancestorCovers ||
																selection.has(keyForPage(grandChild.page.id))
																	? 'checked'
																	: 'unchecked'}
																disabled={ancestorCovers}
																on:change={() =>
																	togglePageSelection(
																		spaceNode,
																		grandChild,
																		grandBreadcrumb
																	)}
															/>
															<span class="text-sm">{grandChild.page.title}</span>
														</div>
													{/each}
												{/if}
											</div>
										{/if}
									{/each}
								{/if}
							</div>
						{/if}
					{/each}
				</div>
			{/if}
		</div>

		<div
			class="border-t border-gray-100/50 dark:border-gray-850/50 px-4 py-3 flex items-center gap-2"
		>
			<div class="text-xs text-gray-500">
				{#if selectedCount > 0}
					{$i18n.t('{{count}} selected', { count: selectedCount })}
				{:else if pagesOnly}
					{$i18n.t('Select pages above.')}
				{:else}
					{$i18n.t('Select spaces or pages above.')}
				{/if}
			</div>
			<button
				class="ml-auto px-3 py-1 text-xs rounded-full bg-gray-50 hover:bg-gray-100 dark:bg-gray-850 dark:hover:bg-gray-800"
				on:click={() => (show = false)}
			>
				{$i18n.t('Cancel')}
			</button>
			<button
				class="px-3 py-1 text-xs rounded-full bg-black text-white dark:bg-white dark:text-black disabled:opacity-40 disabled:cursor-not-allowed"
				disabled={selectedCount === 0}
				on:click={confirmSelection}
			>
				{confirmLabel ?? $i18n.t('Add to Knowledge')}
			</button>
		</div>
	</div>
</Modal>
