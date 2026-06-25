<script lang="ts">
	import { getContext, createEventDispatcher } from 'svelte';
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
		authorizeConfluencePopup,
		isConfluenceAuthError,
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
	// Set when the initial site load fails (e.g. OAuth not connected → 401).
	// Rendered as a single in-modal message instead of an endless toast storm.
	let loadError = '';
	// Set when the user has no Confluence token and the auto-popup couldn't
	// complete it (blocked or cancelled) — render a click-to-connect affordance.
	let needsAuth = false;
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

	// Spaces paginate: load the first page on open, then more on demand so a site
	// with thousands of spaces never blocks the modal. `spaceCursor` is the opaque
	// v2 cursor for the next page (null once exhausted).
	let spaceCursor: string | null = null;
	let hasMoreSpaces = false;
	let loadingMore = false;
	// Pulling every remaining page because the admin chose "Select all".
	let selectingAll = false;
	// Client-side filter over the loaded spaces (name or key).
	let spaceFilter = '';

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

	// Client-side filter over loaded spaces. `spaceFilter` is referenced directly
	// (via filterQuery) so Svelte tracks it as a reactive dependency.
	$: filterQuery = spaceFilter.trim().toLowerCase();
	$: filteredSpaceNodes = filterQuery
		? spaceNodes.filter(
				(n) =>
					(n.space.name ?? '').toLowerCase().includes(filterQuery) ||
					(n.space.key ?? '').toLowerCase().includes(filterQuery)
			)
		: spaceNodes;
	// Header "Select all" reflects whether every visible space is space-selected.
	$: allVisibleSelected =
		filteredSpaceNodes.length > 0 &&
		filteredSpaceNodes.every((n) => selection.has(keyForSpace(n.space)));

	// ─── boot ─────────────────────────────────────────────────────────
	// Loads accessible sites; throws on failure (incl. 401 when the user has no
	// Confluence token). Split out so the auth-retry path can reuse it.
	async function loadSites() {
		const res = await listSites(localStorage.token);
		sites = res.sites ?? [];
		if (sites.length === 0) {
			loadError = $i18n.t('No Confluence sites are accessible for this account.');
			return;
		}
		if (sites.length === 1) {
			await selectSite(sites[0]);
		}
	}

	async function bootstrap() {
		loading = true;
		loadError = '';
		needsAuth = false;
		try {
			await loadSites();
		} catch (e) {
			// 401 → user hasn't connected Confluence. Auto-open the consent popup
			// (mirrors the Google Drive picker), then retry once. The per-user token
			// the popup stores is immediately readable by /browse/sites. The reactive
			// boot guard latches `booted` so this can't re-fire in a tight loop.
			if (isConfluenceAuthError(e)) {
				const result = await authorizeConfluencePopup();
				if (result === 'blocked') {
					// window.open after the await loses the user gesture and some
					// browsers block it — fall back to a click-to-connect button.
					needsAuth = true;
					return;
				}
				try {
					await loadSites();
				} catch (e2) {
					if (isConfluenceAuthError(e2)) {
						needsAuth = true; // cancelled or still no token
					} else {
						loadError =
							$i18n.t('Failed to load Confluence sites: ') +
							(e2 instanceof Error ? e2.message : String(e2));
					}
				}
			} else {
				loadError =
					$i18n.t('Failed to load Confluence sites: ') +
					(e instanceof Error ? e.message : String(e));
			}
		} finally {
			loading = false;
		}
	}

	// Click-to-connect fallback. Opening the popup directly from the click keeps
	// the user gesture, so it isn't blocked.
	async function connectConfluence() {
		const result = await authorizeConfluencePopup();
		if (result === 'blocked') {
			toast.error($i18n.t('Please allow popups to connect Confluence.'));
			return;
		}
		booted = true; // keep the open-guard latched while we retry
		await bootstrap();
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
			const key = item.type === 'space' ? `space:${item.item_id}` : `page:${item.item_id}`;
			seeded.set(key, { kind: item.type === 'space' ? 'space' : 'page', item });
		}
		selection = seeded;
	}

	// Map a freshly-fetched space summary into a collapsed tree node.
	function toSpaceNode(space: ConfluenceSpaceSummary): SpaceNode {
		return { space, expanded: false, loaded: false, loadingChildren: false, children: [] };
	}

	// Fetch the next page of spaces (using the stored cursor) and append it.
	// Archived spaces are dropped. Updates the cursor + has-more flag.
	async function fetchSpacePage() {
		const res = await listSpaces(
			localStorage.token,
			activeSite!.cloud_id,
			spaceCursor ?? undefined
		);
		siteUrl = res.site_url ?? siteUrl;
		spaceCursor = res.next_cursor;
		hasMoreSpaces = !!res.next_cursor;
		spaceNodes = [
			...spaceNodes,
			...res.spaces.filter((s) => s.status !== 'archived').map(toSpaceNode)
		];
	}

	// Initial load: reset paging state and pull the first page only. Subsequent
	// pages load on demand via "Load more" (or in bulk for "Select all"), so a
	// site with thousands of spaces never blocks the modal on open.
	async function loadSpaces() {
		loading = true;
		spaceNodes = [];
		spaceCursor = null;
		hasMoreSpaces = false;
		spaceFilter = '';
		try {
			await fetchSpacePage();
		} catch (e) {
			toast.error(
				$i18n.t('Failed to load Confluence spaces: ') + (e instanceof Error ? e.message : String(e))
			);
		} finally {
			loading = false;
		}
	}

	async function loadMoreSpaces() {
		if (loadingMore || !hasMoreSpaces) return;
		loadingMore = true;
		try {
			await fetchSpacePage();
		} catch (e) {
			toast.error(
				$i18n.t('Failed to load Confluence spaces: ') + (e instanceof Error ? e.message : String(e))
			);
		} finally {
			loadingMore = false;
		}
	}

	// Pull every remaining page (bounded by a generous safety cap) so "Select
	// all" can include spaces that haven't been lazily loaded yet.
	async function loadAllSpaces() {
		let guard = 0;
		while (hasMoreSpaces && guard < 200) {
			await fetchSpacePage();
			guard++;
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

	// Build the space-level selection entry (whole space + all descendants).
	// Shared by single-space toggling and "Select all".
	function spaceEntry(node: SpaceNode): SelectionEntry {
		return {
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
		};
	}

	// Imperative filter check (mirrors the reactive `filteredSpaceNodes`) for use
	// in async flows where reactive vars may not have recomputed yet.
	function matchesFilter(node: SpaceNode): boolean {
		const q = spaceFilter.trim().toLowerCase();
		if (!q) return true;
		return (
			(node.space.name ?? '').toLowerCase().includes(q) ||
			(node.space.key ?? '').toLowerCase().includes(q)
		);
	}

	function toggleSpaceSelection(node: SpaceNode) {
		const key = keyForSpace(node.space);
		if (selection.has(key)) {
			selection.delete(key);
		} else {
			selection.set(key, spaceEntry(node));
		}
		// Either direction: descendant entries are now redundant (selecting) or
		// orphaned (deselecting). Drop them so visual + sync state stay coherent.
		clearSpaceDescendants(node);
		selection = new Map(selection);
	}

	// Select (or clear) every visible space at the space level. With no active
	// filter this means "the whole site" — pull any unloaded pages first so the
	// selection is complete, not just what was lazily loaded.
	async function toggleSelectAll() {
		if (allVisibleSelected) {
			for (const node of spaceNodes.filter(matchesFilter)) {
				selection.delete(keyForSpace(node.space));
				clearSpaceDescendants(node);
			}
			selection = new Map(selection);
			return;
		}
		if (!spaceFilter.trim() && hasMoreSpaces) {
			selectingAll = true;
			try {
				await loadAllSpaces();
			} catch (e) {
				toast.error(
					$i18n.t('Failed to load Confluence spaces: ') +
						(e instanceof Error ? e.message : String(e))
				);
			} finally {
				selectingAll = false;
			}
		}
		for (const node of spaceNodes.filter(matchesFilter)) {
			selection.set(keyForSpace(node.space), spaceEntry(node));
			clearSpaceDescendants(node);
		}
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

	function pageTreeBreadcrumb(spaceNode: SpaceNode, trail: string[], pageNode: PageNode): string {
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

	// Boot exactly once per open. The previous guard re-fired bootstrap() on
	// every failed attempt (sites stays empty after a 401), hammering
	// /browse/sites hundreds of times a second and stacking toasts. `booted`
	// latches when the modal opens and resets on close, so reopening retries
	// once. (No onMount needed — this reactive runs on initial render too.)
	let booted = false;
	$: if (show && !booted) {
		booted = true;
		bootstrap();
	}
	$: if (!show) {
		booted = false;
		loadError = '';
		needsAuth = false;
	}
</script>

<Modal bind:show size="lg">
	<div class="flex flex-col h-[80vh]">
		<div class="flex items-center gap-2 px-4 pt-4 pb-3">
			<Confluence className="size-5" />
			<div class="font-medium text-base">
				{title ??
					(pagesOnly
						? $i18n.t('Select Confluence pages')
						: $i18n.t('Select Confluence spaces or pages'))}
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
						const next = sites.find(
							(s) => s.cloud_id === (e.currentTarget as HTMLSelectElement).value
						);
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
			{:else if needsAuth}
				<div
					class="flex flex-col items-center justify-center h-full gap-3 text-sm text-gray-500 text-center px-6"
				>
					<Confluence className="size-8 opacity-60" />
					<div class="text-gray-700 dark:text-gray-300 font-medium">
						{$i18n.t('Connect your Confluence account to browse spaces and pages.')}
					</div>
					<button
						class="px-3 py-1.5 rounded-lg bg-gray-800 text-white hover:bg-gray-700 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white"
						on:click={connectConfluence}
					>
						{$i18n.t('Connect Confluence')}
					</button>
				</div>
			{:else if loadError}
				<div
					class="flex flex-col items-center justify-center h-full gap-2 text-sm text-gray-500 text-center px-6"
				>
					<div class="text-gray-700 dark:text-gray-300 font-medium">{loadError}</div>
					<div class="text-xs">
						{$i18n.t('Connect your Confluence account in Settings, then reopen this picker.')}
					</div>
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
				<!-- Toolbar: select-all + filter. Sticky so it stays put while the list scrolls. -->
				<div
					class="sticky top-0 z-10 -mx-4 mb-1 flex items-center gap-2 border-b border-gray-100/50 bg-white px-4 py-2 dark:border-gray-850/50 dark:bg-gray-900"
				>
					{#if !pagesOnly}
						<label
							class="flex shrink-0 items-center gap-1.5 text-xs text-gray-600 dark:text-gray-300"
						>
							<Checkbox
								state={allVisibleSelected ? 'checked' : 'unchecked'}
								on:change={toggleSelectAll}
							/>
							{$i18n.t('Select all')}
						</label>
					{/if}
					<input
						class="flex-1 rounded-lg bg-gray-50 px-2.5 py-1 text-sm outline-hidden dark:bg-gray-850"
						placeholder={$i18n.t('Filter spaces...')}
						bind:value={spaceFilter}
					/>
					{#if selectingAll}
						<Spinner className="size-4" />
					{/if}
				</div>

				{#if filteredSpaceNodes.length === 0}
					<div class="py-6 text-center text-sm text-gray-500">
						{$i18n.t('No spaces match your filter.')}
					</div>
				{:else}
					<div class="flex flex-col gap-0.5">
						{#each filteredSpaceNodes as spaceNode}
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
																		togglePageSelection(spaceNode, grandChild, grandBreadcrumb)}
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
					{#if hasMoreSpaces && !filterQuery}
						<div class="flex justify-center py-2">
							<button
								class="rounded-lg bg-gray-50 px-3 py-1.5 text-xs text-gray-700 hover:bg-gray-100 disabled:opacity-50 dark:bg-gray-850 dark:text-gray-200 dark:hover:bg-gray-800"
								on:click={loadMoreSpaces}
								disabled={loadingMore}
							>
								{#if loadingMore}
									<Spinner className="size-3.5" />
								{:else}
									{$i18n.t('Load more spaces')}
								{/if}
							</button>
						</div>
					{/if}
				{/if}
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
