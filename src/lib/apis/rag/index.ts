/**
 * RAG Filter API — Collection and Document Discovery.
 *
 * Calls the open-webui backend's discovery proxy (/api/v1/discovery/*).
 * Same-origin, session-cookie authed; the upstream X-API-Key is injected
 * server-side and never leaves the backend.
 */

import { WEBUI_API_BASE_URL } from '$lib/constants';

export interface RagDocument {
	id: string;
	title: string;
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
 * Fetch all collections and their documents via the backend discovery proxy.
 * Returns null on any error (caller toasts).
 */
export const getCollectionsAndDocuments = async (
	token?: string
): Promise<RagDiscoveryResponse | null> => {
	const headers: Record<string, string> = {
		Accept: 'application/json',
		'Content-Type': 'application/json'
	};
	if (token) {
		headers.Authorization = `Bearer ${token}`;
	}

	try {
		const res = await fetch(`${WEBUI_API_BASE_URL}/discovery/documents`, {
			method: 'GET',
			credentials: 'include',
			headers
		});

		if (!res.ok) {
			const body = await res.json().catch(() => ({}));
			console.error('[RAG API]', res.status, body);
			return null;
		}

		const data = (await res.json()) as RagDiscoveryResponse;

		if (data?.collections && Array.isArray(data.collections)) {
			data.collections = data.collections.map((collection: RagCollection) => ({
				...collection,
				documents: (collection.documents ?? []).map((doc: any) => ({
					...doc,
					id: doc?.id ?? doc?.doc_id ?? doc?.original_doc_id ?? doc?.title
				}))
			}));
		}

		return data;
	} catch (err) {
		console.error('[RAG API]', err);
		return null;
	}
};
