<script lang="ts">
	// [Gradient] Each answer keeps its own source rows, even when another answer reused them.
	import { getContext } from 'svelte';
	import type { i18n as I18n } from 'i18next';
	import type { Readable } from 'svelte/store';
	import type { SourceGroup } from '../../ChatControls/citationTab';
	import type { DisplayCitation } from './reduceSources';
	import CitationSourceList from './CitationSourceList.svelte';
	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import ChevronRight from '$lib/components/icons/ChevronRight.svelte';

	const i18n = getContext<Readable<I18n>>('i18n');
	export let groups: SourceGroup[] = [];
	export let expanded: Set<string> = new Set();
	export let selectedMessageId: string | undefined = undefined;
	export let selectedCitation: DisplayCitation | null = null;
	export let relevanceEnabled = true;
	export let onToggle: (messageId: string) => void;
	export let onSelect: (citation: DisplayCitation, group: SourceGroup) => void;
	const headers = new Map<string, HTMLButtonElement>();

	function registerHeader(node: HTMLButtonElement, messageId: string) {
		headers.set(messageId, node);
		return {
			destroy: () => {
				headers.delete(messageId);
			}
		};
	}

	export function scrollToGroup(messageId: string) {
		headers.get(messageId)?.scrollIntoView({ block: 'nearest' });
	}
</script>

<div class="flex-1 min-h-0 overflow-y-auto scrollbar-thin p-2 space-y-1">
	{#each groups.filter((group) => group.used.length > 0) as group (group.messageId)}
		<section>
			<button
				use:registerHeader={group.messageId}
				class="flex w-full items-center gap-2 rounded-lg p-3 text-left hover:bg-gray-50 dark:hover:bg-gray-850"
				aria-expanded={expanded.has(group.messageId)}
				on:click={() => onToggle(group.messageId)}
			>
				<span class="min-w-0 flex-1 line-clamp-2 text-sm">{group.question}</span>
				<span class="shrink-0 text-xs text-gray-500 dark:text-gray-400"
					>{$i18n.t('{{count}} sources', { count: group.used.length })}</span
				>
				{#if expanded.has(group.messageId)}
					<ChevronDown className="size-4 shrink-0 text-gray-500" />
				{:else}
					<ChevronRight className="size-4 shrink-0 text-gray-500" />
				{/if}
			</button>
			{#if expanded.has(group.messageId)}
				<CitationSourceList
					embedded
					visibleCitations={group.used}
					selectedCitation={selectedMessageId === group.messageId
						? (group.used.find((citation) => citation.id === selectedCitation?.id) ?? null)
						: null}
					showPercentage={relevanceEnabled && group.showPercentage}
					showRelevance={relevanceEnabled && group.showRelevance}
					onSelect={(citation) => onSelect(citation, group)}
				/>
			{/if}
		</section>
	{/each}
</div>
