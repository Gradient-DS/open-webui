import { writable } from 'svelte/store';

/**
 * Subtype filter - either fully selected or with specific documents
 * - true: all documents in this subtype are selected
 * - object: specific documents are selected
 */
export type SubtypeFilter = true | {
	doc_ids: (string | number)[];
	doc_titles: string[];
};

/**
 * Collection filter with hierarchical efficiency:
 * - { all: true } or {}: all documents in collection selected
 * - { subtypes: { "SubtypeA": true } }: all docs in SubtypeA selected
 * - { subtypes: { "SubtypeA": { doc_ids: [...] } } }: specific docs selected
 */
export interface CollectionFilter {
	// If true, all documents in collection are selected
	all?: boolean;

	// Subtypes filter - only present if not all selected
	// Key is subtype name, value indicates selection level
	subtypes?: Record<string, SubtypeFilter>;
}

export interface RagFilterState {
	collections: Record<string, CollectionFilter>;
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
