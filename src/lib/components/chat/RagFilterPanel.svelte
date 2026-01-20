<script lang="ts">
	/**
	 * RAG Filter Panel - Floating/Sidebar panel for RAG collection/document filtering
	 * 
	 * This component provides a collapsible panel that shows collection and document
	 * filters for RAG queries. It can be toggled via the navbar button.
	 */
	import { showRagFilter, ragFilterState, updateRagFilter } from '$lib/stores/rag-filter';
	import { slide } from 'svelte/transition';
	import RagFilter from './RagFilter.svelte';
	import XMark from '../icons/XMark.svelte';
	
	const handleFilterChange = (event: CustomEvent) => {
		const { mode, collections, documents } = event.detail;
		updateRagFilter({ mode, collections, documents });
		
		// Log for debugging
		console.log('[RAG Filter]', { mode, collections, documents });
	};
</script>

{#if $showRagFilter}
	<div
		class="fixed right-0 top-0 h-screen w-80 bg-white dark:bg-gray-900 border-l border-gray-200 dark:border-gray-700 shadow-xl z-40"
		transition:slide={{ duration: 200, axis: 'x' }}
	>
		<!-- Header with close button -->
		<div class="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-700">
			<h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100">
				Filter Documenten
			</h2>
			<button
				on:click={() => showRagFilter.set(false)}
				class="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 transition"
				aria-label="Close RAG Filter"
			>
				<XMark className="size-5" />
			</button>
		</div>
		
		<!-- Filter component -->
		<div class="h-[calc(100vh-4rem)] overflow-hidden">
			<RagFilter on:filterChange={handleFilterChange} />
		</div>
	</div>
{/if}

<style>
	/* Ensure panel appears above other elements but below modals */
	:global(.rag-filter-overlay) {
		z-index: 40;
	}
</style>
