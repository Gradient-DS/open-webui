/**
 * RAG Filter API - Collection and Document Discovery
 * 
 * Provides functions to interact with the external Vector DB API
 * for discovering available collections and documents for RAG filtering.
 */

// External API base URL - configured via environment variable
// This should point to your Flask API (e.g., http://localhost:3535)
const RAG_API_BASE_URL = import.meta.env.PUBLIC_RAG_API_BASE_URL || 'http://localhost:3535';
const RAG_API_KEY = import.meta.env.PUBLIC_RAG_API_KEY || 'your-dev-api-key-here';

export interface RagDocument {
	id: string; // Document ID for filtering
	title: string; // Document title for display
	contentsubtype?: string;
}

export interface RagCollection {
	collection_key: string;
	collection_name: string;
	schema_name: string;
	document_count: number;
	documents: RagDocument[];
	error?: string;
}

export interface RagDiscoveryResponse {
	collections: RagCollection[];
	total_collections: number;
	database: {
		name: string;
		display_name: string;
	};
}

/**
 * Fetch all collections and their documents from the Vector DB API.
 * Used to populate the RAG filter UI with available collections and documents.
 * 
 * @returns Promise resolving to discovery data or null on error
 */
export const getCollectionsAndDocuments = async (): Promise<RagDiscoveryResponse | null> => {
	let error = null;

	const res = await fetch(`${RAG_API_BASE_URL}/discovery/documents`, {
		method: 'GET',
		headers: {
			'Accept': 'application/json',
			'Content-Type': 'application/json',
			'X-API-Key': RAG_API_KEY
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.then((json) => {
			// Normalize API responses that use `doc_id` instead of `id`
			// so the UI components can rely on `RagDocument.id` being present.
			const data = json as any;

			if (data?.collections && Array.isArray(data.collections)) {
				data.collections = data.collections.map((collection: any) => {
					const documents = Array.isArray(collection?.documents) ? collection.documents : [];
					return {
						...collection,
						documents: documents.map((doc: any) => ({
							...doc,
							id: doc?.id ?? doc?.doc_id ?? doc?.original_doc_id ?? doc?.title
						}))
					};
				});
			}

			return data as RagDiscoveryResponse;
		})
		.catch((err) => {
			error = err.detail || err.message || 'Failed to fetch collections and documents';
			console.error('[RAG API]', err);
			return null;
		});

	if (error) {
		console.error('[RAG API] Error:', error);
		return null;
	}

	return res;
};

/**
 * Check if the RAG filter feature is enabled and accessible.
 * 
 * @returns Promise resolving to true if accessible, false otherwise
 */
export const checkRagFilterAvailability = async (): Promise<boolean> => {
	try {
		const response = await fetch(`${RAG_API_BASE_URL}/discovery/documents`, {
			method: 'GET',
			headers: {
				'Accept': 'application/json',
				'X-API-Key': RAG_API_KEY
			}
		});
		
		return response.ok;
	} catch (err) {
		console.warn('[RAG API] Filter feature not available:', err);
		return false;
	}
};
