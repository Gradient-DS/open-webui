import { writable } from 'svelte/store';

export interface RagFilterState {
	mode: 'collections' | 'documents';
	collections: string[]; // Collection keys
	documents: string[];  // Document titles
}

const defaultState: RagFilterState = {
	mode: 'collections',
	collections: [],
	documents: []
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
