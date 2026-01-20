import { writable } from 'svelte/store';

export interface CollectionFilter {
	doc_ids: string[];    // Document IDs
	doc_titles: string[]; // Document titles
}

export interface RagFilterState {
	collections: Record<string, CollectionFilter>; // Map of collection key -> filter data
}

const defaultState: RagFilterState = {
	collections: {}
};

// Store for RAG filter state
export const ragFilterState = writable<RagFilterState>(defaultState);

// Store to control RAG filter panel visibility
export const showRagFilter = writable<boolean>(false);

// Helper function to update filter state
export function updateRagFilter(state: Partial<RagFilterState>) {
	ragFilterState.update(current => ({ ...current, ...state }));
}

// Helper function to reset filter state
export function resetRagFilter() {
	ragFilterState.set(defaultState);
}
