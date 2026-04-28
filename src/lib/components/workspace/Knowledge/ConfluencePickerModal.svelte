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
	//   space:{id}                      → whole space (all descendants)
	//   page:{id}                       → just this page
	//   page-tree:{id}                  → this page + all descendants
	type SelectionKind = 'space' | 'page' | 'page-tree';
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

	async function togglePageExpand(parent: PageNode) {
		parent.expanded = !parent.expanded;
		if (parent.expanded && !parent.loaded) {
			parent.loadingChildren = true;
			spaceNodes = spaceNodes;
			try {
				const pages = await fetchAllPageChildren(parent.page.id);
				parent.children = pages.map(toPageNode);
				parent.loaded = true;
			} catch (e) {
				toast.error(
					$i18n.t('Failed to load pages: ') + (e instanceof Error ? e.message : String(e))
				);
			} finally {
				parent.loadingChildren = false;
				spaceNodes = spaceNodes;
			}
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
	function keyForPageTree(pageId: string): string {
		return `page-tree:${pageId}`;
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
		selection = new Map(selection);
	}

	function togglePageSelection(
		spaceNode: SpaceNode,
		pageNode: PageNode,
		breadcrumb: string,
		includeDescendants: boolean
	) {
		const singleKey = keyForPage(pageNode.page.id);
		const treeKey = keyForPageTree(pageNode.page.id);
		const activeKey = includeDescendants ? treeKey : singleKey;
		const otherKey = includeDescendants ? singleKey : treeKey;

		if (selection.has(activeKey)) {
			selection.delete(activeKey);
		} else {
			// Toggling on: remove the other variant if present.
			selection.delete(otherKey);
			selection.set(activeKey, {
				kind: includeDescendants ? 'page-tree' : 'page',
				item: {
					type: 'page',
					cloud_id: activeSite!.cloud_id,
					space_id: spaceNode.space.id,
					space_key: spaceNode.space.key,
					site_url: siteUrl,
					item_id: pageNode.page.id,
					item_path: breadcrumb,
					name: pageNode.page.title,
					include_descendants: includeDescendants
				}
			});
		}
		selection = new Map(selection);
	}

	function isPageSelected(pageId: string, kind: 'any' | 'single' | 'tree'): boolean {
		if (kind === 'single') return selection.has(keyForPage(pageId));
		if (kind === 'tree') return selection.has(keyForPageTree(pageId));
		return selection.has(keyForPage(pageId)) || selection.has(keyForPageTree(pageId));
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
			<div class="font-medium text-base">{$i18n.t('Select Confluence spaces or pages')}</div>
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
							<Checkbox
								state={selection.has(keyForSpace(spaceNode.space)) ? 'checked' : 'unchecked'}
								on:change={() => toggleSpaceSelection(spaceNode)}
							/>
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
										<div class="flex items-center gap-2 py-1">
											<button
												class="p-0.5 rounded hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-40"
												on:click={() => togglePageExpand(pageNode)}
												disabled={!pageNode.page.has_children &&
													!pageNode.loaded &&
													pageNode.children.length === 0}
												aria-label={pageNode.expanded ? $i18n.t('Collapse') : $i18n.t('Expand')}
											>
												{#if pageNode.expanded}
													<ChevronDown className="size-3.5" />
												{:else}
													<ChevronRight className="size-3.5" />
												{/if}
											</button>
											<Checkbox
												state={isPageSelected(pageNode.page.id, 'single') ? 'checked' : 'unchecked'}
												on:change={() =>
													togglePageSelection(spaceNode, pageNode, breadcrumbSingle, false)}
											/>
											<span class="text-sm">{pageNode.page.title}</span>

											<label
												class="ml-auto text-xs text-gray-500 flex items-center gap-1 cursor-pointer select-none"
											>
												<Checkbox
													state={isPageSelected(pageNode.page.id, 'tree') ? 'checked' : 'unchecked'}
													on:change={() =>
														togglePageSelection(spaceNode, pageNode, breadcrumbSingle, true)}
												/>
												{$i18n.t('Include children')}
											</label>
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
														<div class="flex items-center gap-2 py-1">
															<button
																class="p-0.5 rounded hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-40"
																on:click={() => togglePageExpand(grandChild)}
																disabled={!grandChild.page.has_children &&
																	!grandChild.loaded &&
																	grandChild.children.length === 0}
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
															<Checkbox
																state={isPageSelected(grandChild.page.id, 'single')
																	? 'checked'
																	: 'unchecked'}
																on:change={() =>
																	togglePageSelection(
																		spaceNode,
																		grandChild,
																		grandBreadcrumb,
																		false
																	)}
															/>
															<span class="text-sm">{grandChild.page.title}</span>
															<label
																class="ml-auto text-xs text-gray-500 flex items-center gap-1 cursor-pointer select-none"
															>
																<Checkbox
																	state={isPageSelected(grandChild.page.id, 'tree')
																		? 'checked'
																		: 'unchecked'}
																	on:change={() =>
																		togglePageSelection(
																			spaceNode,
																			grandChild,
																			grandBreadcrumb,
																			true
																		)}
																/>
																{$i18n.t('Include children')}
															</label>
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
				{$i18n.t('Add to Knowledge')}
			</button>
		</div>
	</div>
</Modal>
