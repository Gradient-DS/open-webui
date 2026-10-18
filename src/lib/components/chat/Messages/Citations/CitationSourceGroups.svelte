<script lang="ts">
	// [Gradient] Each answer keeps its own source rows, even when another answer reused them.
	import { getContext } from 'svelte';
	import type { i18n as I18n } from 'i18next';
	import type { Readable } from 'svelte/store';
	import type { SourceGroup } from '../../ChatControls/citationTab';
	import type { DisplayCitation } from './reduceSources';
	import CitationSourceList from './CitationSourceList.svelte';
	import ChevronRight from '$lib/components/icons/ChevronRight.svelte';
	import { slide } from 'svelte/transition';
	import ChatBubbleOval from '$lib/components/icons/ChatBubbleOval.svelte';

	const i18n = getContext<Readable<I18n>>('i18n');
	export let groups: SourceGroup[] = [];
	export let expanded: Set<string> = new Set();
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
			<!-- [Gradient] The header reads as the user's question: bubble icon, italic, own surface. -->
			<button
				use:registerHeader={group.messageId}
				class="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left border transition {expanded.has(
					group.messageId
				)
					? 'bg-gray-100 dark:bg-gray-800 border-gray-200 dark:border-gray-700'
					: 'bg-gray-50 dark:bg-gray-850 border-gray-100 dark:border-gray-800 hover:bg-gray-100 dark:hover:bg-gray-800'}"
				aria-expanded={expanded.has(group.messageId)}
				on:click={() => onToggle(group.messageId)}
			>
				<ChatBubbleOval className="size-3.5 shrink-0 text-gray-400 dark:text-gray-500" />
				<span class="min-w-0 flex-1 truncate text-sm italic text-gray-800 dark:text-gray-200"
					>{group.question}</span
				>
				<span class="shrink-0 text-xs text-gray-500 dark:text-gray-400"
					>{$i18n.t('{{count}} sources', { count: group.used.length })}</span
				>
				<ChevronRight
					className="size-4 shrink-0 text-gray-500 transition-transform duration-200 {expanded.has(
						group.messageId
					)
						? 'rotate-90'
						: ''}"
				/>
			</button>
			{#if expanded.has(group.messageId)}
				<!-- [Gradient] Slide the group body open/closed instead of snapping. -->
				<div transition:slide={{ duration: 200 }}>
					<CitationSourceList
						embedded
						visibleCitations={group.used}
						onSelect={(citation) => onSelect(citation, group)}
					/>
				</div>
			{/if}
		</section>
	{/each}
</div>
