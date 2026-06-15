<script lang="ts">
	// Recursive tree-row for `TopdeskPickerModal`. TOPdesk knowledge-item trees
	// nest arbitrarily deep, so the row + its expanded-children block render
	// themselves via `<svelte:self>` for every child instead of the old
	// hardcoded three levels. All tree mutation (expand/select) stays in the
	// modal — this component only renders and forwards intent through the
	// `onToggleExpand` / `onToggleSelect` callbacks.

	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';

	import Checkbox from '$lib/components/common/Checkbox.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import ChevronRight from '$lib/components/icons/ChevronRight.svelte';
	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';

	import type { TopdeskKbItem } from '$lib/apis/topdesk';
	import type { ItemNode } from './types';

	const i18n = getContext<Writable<i18nType>>('i18n');

	export let node: ItemNode;
	export let depth = 0;
	// True when some ancestor of this node is selected — its subtree is already
	// covered, so this node's checkbox shows checked + disabled.
	export let ancestorCovers = false;
	// Selection map owned by the modal. Passed by reference; the modal reassigns
	// it (`new Map(...)`) on every change so this prop updates and re-renders.
	export let selection: Map<string, TopdeskKbItem>;
	export let onToggleSelect: (node: ItemNode) => void;
	export let onToggleExpand: (node: ItemNode) => void;

	// A node known to have no children is a leaf — render a spacer, never a
	// chevron. `has_children` is best-effort, so a flagged-expandable node that
	// loaded zero children collapses to a leaf here too.
	$: isLeaf = node.loaded && node.children.length === 0;
	$: covered = ancestorCovers || selection.has(node.item.id);
	// Children inherit coverage from this node being selected (or already covered).
	$: childAncestorCovers = ancestorCovers || selection.has(node.item.id);
</script>

<div class="flex items-center gap-2 py-1" style="padding-left: {depth * 1.75}rem;">
	{#if isLeaf}
		<span class="inline-block size-[18px]"></span>
	{:else}
		<button
			class="p-0.5 rounded hover:bg-gray-100 dark:hover:bg-gray-800"
			on:click={() => onToggleExpand(node)}
			aria-label={node.expanded ? $i18n.t('Collapse') : $i18n.t('Expand')}
		>
			{#if node.expanded}
				<ChevronDown className="size-3.5" />
			{:else}
				<ChevronRight className="size-3.5" />
			{/if}
		</button>
	{/if}
	<Checkbox
		state={covered ? 'checked' : 'unchecked'}
		disabled={ancestorCovers}
		on:change={() => onToggleSelect(node)}
	/>
	<span class="text-sm {depth === 0 ? 'font-medium' : ''}">{node.item.name}</span>
	{#if node.item.number}
		<span class="text-xs text-gray-400">({node.item.number})</span>
	{/if}
</div>

{#if node.expanded}
	{#if node.loadingChildren}
		<div class="py-2" style="padding-left: {(depth + 1) * 1.75}rem;"><Spinner className="size-4" /></div>
	{:else if node.children.length === 0}
		<div class="py-1 text-xs text-gray-400" style="padding-left: {(depth + 1) * 1.75}rem;">
			{$i18n.t('No children.')}
		</div>
	{:else}
		{#each node.children as child (child.item.id)}
			<svelte:self
				node={child}
				depth={depth + 1}
				ancestorCovers={childAncestorCovers}
				{selection}
				{onToggleSelect}
				{onToggleExpand}
			/>
		{/each}
	{/if}
{/if}
