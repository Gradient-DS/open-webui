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

export interface FileProbe {
	available: boolean;
	/** What the content route serves, without parameters; unknown while the original is prepared. */
	contentType?: string;
}

export async function probeFile(fileId: string): Promise<FileProbe> {
	try {
		const token = typeof localStorage !== 'undefined' ? localStorage.getItem('token') : null;
		const response = await fetch(`${WEBUI_API_BASE_URL}/files/${fileId}/content`, {
			method: 'HEAD',
			headers: token ? { authorization: `Bearer ${token}` } : {}
		});
		// [Gradient] 409: the original is still being prepared; the viewer waits for it.
		if (!response.ok) return { available: response.status === 409 };
		const contentType = response.headers?.get('content-type')?.split(';')[0].trim().toLowerCase();
		return contentType ? { available: true, contentType } : { available: true };
	} catch {
		return { available: false };
	}
}

export async function probeFileAvailable(fileId: string): Promise<boolean> {
	return (await probeFile(fileId)).available;
}

// [Gradient] Every check re-probes (no cached failure) and only the latest check reports, so a late
// result for a replaced citation never decides the preview of the one now shown.
export function createPreviewProbe(
	onResult: (available: boolean, contentType?: string) => void,
	probe: (fileId: string) => Promise<boolean | FileProbe> = probeFile
) {
	let version = 0;
	return {
		async check(fileId: string | undefined): Promise<void> {
			const current = ++version;
			onResult(true);
			if (!fileId) return;
			const result = await probe(fileId);
			const { available, contentType } =
				typeof result === 'boolean' ? { available: result, contentType: undefined } : result;
			if (current === version) onResult(available, contentType);
		},
		cancel() {
			version++;
		}
	};
}
