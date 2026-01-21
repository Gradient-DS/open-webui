/**
 * RAG Filter Utilities
 * 
 * Utility functions for working with RAG filter state in request bodies.
 * This keeps the filter logic isolated and non-invasive to other components.
 */

import { get } from 'svelte/store';
import { ragFilterState, type RagFilterState } from '$lib/stores/rag-filter';

/**
 * Get RAG filter data formatted for request body
 * 
 * Returns the filter state formatted as an object suitable for inclusion
 * in chat completion request bodies, or undefined if no filters are active.
 * 
 * @returns Formatted filter object or undefined
 */
export function getRagFilterForRequest(): Record<string, {
	doc_ids: (string | number)[];
	doc_titles: string[];
}> | undefined {
	const filterState = get(ragFilterState);
	
	// Return filter if at least one collection has filters
	if (Object.keys(filterState.collections).length > 0) {
		return filterState.collections;
	}
	
	return undefined;
}
