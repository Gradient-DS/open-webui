<script lang="ts">
	// Lazy TOPdesk knowledge-item tree picker (admin Cloud Sync shared-KB flow).
	//
	// Modelled on `workspace/Knowledge/ConfluencePickerModal.svelte` — same tree
	// explorer, checkbox-selection and `currentItems` pre-fill UX — but adapted to
	// TOPdesk's flat `browseTopdeskItems(token, parentId?)` source instead of the
	// Confluence sites/spaces/pages hierarchy. There is no per-user reuse: this
	// only ever drives `SharedKbSection`'s admin provision flow, which forwards the
	// emitted `TopdeskKbItem[]` straight to `provisionTopdeskSharedKb`.
	//
	// Selection model mirrors Confluence's: a single checkbox per node opts that
	// node in *with its subtree*. A node selected this way emits
	// `{ type: 'folder', item_id, name, include_descendants: true }`; a node the
	// admin un-expands to a known leaf still emits a folder entry (the backend
	// treats `include_descendants` on a childless item as a no-op). Selecting an
	// ancestor disables and covers descendant checkboxes so the visual + sync
	// state stay coherent — identical to the Confluence picker.

	import { getContext, createEventDispatcher } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { toast } from 'svelte-sonner';

	import Modal from '$lib/components/common/Modal.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Topdesk from '$lib/components/icons/Topdesk.svelte';
	import TopdeskPickerNode from './TopdeskPickerNode.svelte';

	import { browseTopdeskItems, type TopdeskBrowseItem, type TopdeskKbItem } from '$lib/apis/topdesk';
	import type { ItemNode } from './types';

	const i18n = getContext<Writable<i18nType>>('i18n');
	const dispatch = createEventDispatcher<{ select: { items: TopdeskKbItem[] } }>();

	export let show = false;
	// Overridden by SharedKbSection to retitle the modal, relabel the confirm
	// button, and pre-fill the selection from items already opted into the KB.
	export let title: string | null = null;
	export let confirmLabel: string | null = null;
	export let currentItems: TopdeskKbItem[] = [];

	// ─── tree state ──────────────────────────────────────────────────────
	// `has_children` is a best-effort flag (defaults true server-side when
	// unknown). A node flagged expandable that yields zero children on expand is
	// marked `loaded` with an empty `children` list and collapses gracefully — the
	// chevron then renders as a leaf spacer on the next pass. The `ItemNode` shape
	// lives in `./types` so the recursive `TopdeskPickerNode` shares it.

	let loading = false;
	let rootNodes: ItemNode[] = [];
	let booted = false;

	// Selection keyed by item id. Each entry maps to the folder/file SyncItem
	// shape the backend `TopdeskKbItem` model expects.
	let selection: Map<string, TopdeskKbItem> = new Map();
	$: selectedCount = selection.size;

	function toNode(item: TopdeskBrowseItem): ItemNode {
		return { item, expanded: false, loaded: false, loadingChildren: false, children: [] };
	}

	// ─── boot ──────────────────────────────────────────────────────────────
	async function bootstrap() {
		loading = true;
		try {
			const res = await browseTopdeskItems(localStorage.token);
			rootNodes = (res.items ?? []).map(toNode);
			seedSelectionFromCurrent();
		} catch (e) {
			toast.error(
				$i18n.t('Failed to load TOPdesk items: ') + (e instanceof Error ? e.message : String(e))
			);
		} finally {
			loading = false;
			booted = true;
		}
	}

	// Pre-tick items already opted into the KB so re-provisioning starts with the
	// existing selection visible. The full tree need not be expanded — selection
	// is keyed by item id and checkboxes read from it wherever the node renders.
	function seedSelectionFromCurrent() {
		const seeded = new Map<string, TopdeskKbItem>();
		for (const item of currentItems ?? []) {
			if (!item?.item_id) continue;
			seeded.set(item.item_id, {
				type: item.type === 'file' ? 'file' : 'folder',
				item_id: item.item_id,
				name: item.name ?? item.item_id,
				include_descendants: item.include_descendants ?? true
			});
		}
		selection = seeded;
	}

	// Re-bootstrap each time the modal opens so the tree + pre-selection reflect
	// the latest shared-KB state; reset when it closes.
	$: if (show && !booted && !loading) {
		bootstrap();
	}
	$: if (!show && booted) {
		booted = false;
		rootNodes = [];
		selection = new Map();
	}

	// ─── lazy expand ─────────────────────────────────────────────────────
	async function toggleExpand(node: ItemNode) {
		node.expanded = !node.expanded;
		if (!node.expanded) {
			rootNodes = rootNodes;
			return;
		}
		if (!node.loaded) {
			node.loadingChildren = true;
			rootNodes = rootNodes;
			try {
				const res = await browseTopdeskItems(localStorage.token, node.item.id);
				node.children = (res.items ?? []).map(toNode);
				node.loaded = true;
			} catch (e) {
				// Surface the error and collapse so the admin can retry on click.
				node.expanded = false;
				toast.error(
					$i18n.t('Failed to load TOPdesk items: ') + (e instanceof Error ? e.message : String(e))
				);
			} finally {
				node.loadingChildren = false;
				rootNodes = rootNodes;
			}
			// Best-effort `has_children`: an expandable node that returned nothing
			// is a leaf after all — collapse gracefully.
			if (node.loaded && node.children.length === 0) {
				node.expanded = false;
				rootNodes = rootNodes;
			}
		} else {
			rootNodes = rootNodes;
		}
	}

	// ─── selection ─────────────────────────────────────────────────────────
	function clearDescendants(node: ItemNode) {
		for (const child of node.children) {
			selection.delete(child.item.id);
			clearDescendants(child);
		}
	}

	function toggleSelection(node: ItemNode) {
		const id = node.item.id;
		if (selection.has(id)) {
			selection.delete(id);
		} else {
			// A leaf (known to have no children) is a single file; anything that can
			// have descendants opts the subtree in as a folder.
			const isLeaf = node.loaded && node.children.length === 0;
			selection.set(id, {
				type: isLeaf ? 'file' : 'folder',
				item_id: id,
				name: node.item.name,
				include_descendants: !isLeaf
			});
		}
		// Descendant entries are now redundant (selecting) or orphaned
		// (deselecting) — drop them so visual + sync state stay coherent.
		clearDescendants(node);
		selection = new Map(selection);
	}

	function confirmSelection() {
		if (selection.size === 0) return;
		dispatch('select', { items: Array.from(selection.values()) });
		show = false;
	}
</script>

<Modal bind:show size="lg">
	<div class="flex flex-col h-[80vh]">
		<div class="flex items-center gap-2 px-4 pt-4 pb-3">
			<Topdesk className="size-5" />
			<div class="font-medium text-base">
				{title ?? $i18n.t('Items to sync')}
			</div>
			<button
				class="ml-auto text-sm text-gray-400 hover:text-gray-600 dark:hover:text-gray-200"
				on:click={() => (show = false)}
				aria-label={$i18n.t('Close')}
			>
				✕
			</button>
		</div>

		<div class="flex-1 overflow-y-auto px-4 pb-2 min-h-0">
			{#if loading}
				<div class="flex items-center justify-center h-full">
					<Spinner className="size-5" />
				</div>
			{:else if rootNodes.length === 0}
				<div class="flex items-center justify-center h-full text-sm text-gray-500">
					{$i18n.t('No items found.')}
				</div>
			{:else}
				<div class="flex flex-col gap-0.5">
					{#each rootNodes as node (node.item.id)}
						<TopdeskPickerNode
							{node}
							depth={0}
							ancestorCovers={false}
							{selection}
							onToggleSelect={toggleSelection}
							onToggleExpand={toggleExpand}
						/>
					{/each}
				</div>
			{/if}
		</div>

		<div class="border-t border-gray-100/50 dark:border-gray-850/50 px-4 py-3 flex items-center gap-2">
			<div class="text-xs text-gray-500">
				{#if selectedCount > 0}
					{$i18n.t('{{count}} selected', { count: selectedCount })}
				{:else}
					{$i18n.t('Select items above.')}
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
				{confirmLabel ?? $i18n.t('Provision')}
			</button>
		</div>
	</div>
</Modal>
