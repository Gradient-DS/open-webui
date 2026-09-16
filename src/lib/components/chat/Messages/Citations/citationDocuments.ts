// [Gradient] Shared citation derivation for the modal and side panel.
import type { DisplayCitation, RawSourceMeta, RawSourceObject } from './reduceSources';
import { WEBUI_API_BASE_URL } from '$lib/constants';

export interface CitationDocument {
	source: RawSourceObject;
	document: string;
	metadata?: RawSourceMeta;
	distance?: number;
}

export function mergeCitationDocuments(
	citation: DisplayCitation | null | undefined
): CitationDocument[] {
	const documents = (citation?.document ?? []).map((document, i) => ({
		source: citation!.source,
		document,
		metadata: citation?.metadata?.[i],
		distance: citation?.distances?.[i]
	}));
	return documents.every((doc) => doc.distance !== undefined)
		? documents.sort((a, b) => (b.distance ?? Infinity) - (a.distance ?? Infinity))
		: documents;
}

export async function probeFileAvailable(fileId: string): Promise<boolean> {
	try {
		const token = typeof localStorage !== 'undefined' ? localStorage.getItem('token') : null;
		const response = await fetch(`${WEBUI_API_BASE_URL}/files/${fileId}/content`, {
			method: 'HEAD',
			headers: token ? { authorization: `Bearer ${token}` } : {}
		});
		return response.ok;
	} catch {
		return false;
	}
}
