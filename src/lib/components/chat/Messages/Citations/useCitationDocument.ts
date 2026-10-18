// [Gradient] Citation document helpers shared by both presentation hosts.
import { WEBUI_API_BASE_URL } from '$lib/constants';
import { rectsFromMetadata } from '$lib/utils/citationRects';
import type { CitationDocument } from './citationDocuments';
import type { DisplayCitation } from './reduceSources';

export function citationFileInfo(
	citation: DisplayCitation | null | undefined,
	documents: CitationDocument[]
) {
	const fileName = documents[0]?.metadata?.name ?? citation?.source?.name ?? '';
	const fileId = documents[0]?.metadata?.file_id;
	const isPDF = fileName.toLowerCase().endsWith('.pdf');
	const isDocx = fileName.toLowerCase().endsWith('.docx');
	const isXlsx = fileName.toLowerCase().endsWith('.xlsx');
	const isImage = /\.(png|jpe?g|gif|webp|svg|bmp)$/i.test(fileName);
	const isAudio = /\.(mp3|wav|ogg|m4a|webm)$/i.test(fileName);
	return {
		fileName,
		fileId,
		isPDF,
		isDocx,
		isXlsx,
		isImage,
		isAudio,
		isPreviewable: !!fileId && (isPDF || isDocx || isXlsx || isImage || isAudio),
		showSnippetRail: isPDF || isDocx
	};
}

export function resolveExternalUrl(
	citation: DisplayCitation | null | undefined,
	documents: CitationDocument[]
): string | null {
	const url = citation?.source?.url;
	if (typeof url === 'string' && url.includes('http')) return url;
	const sourceUrl = documents[0]?.metadata?.source_url;
	return typeof sourceUrl === 'string' && sourceUrl.includes('http') ? sourceUrl : null;
}

export const isDocumentSnippet = (snippet: CitationDocument | undefined): boolean =>
	snippet?.metadata?.granularity === 'document';
export const rectsFromSnippet = (snippet: CitationDocument | undefined) =>
	isDocumentSnippet(snippet) ? null : rectsFromMetadata(snippet?.metadata);
export const snippetPage = (snippet: CitationDocument | undefined): number | undefined =>
	Number.isInteger(snippet?.metadata?.page) ? (snippet?.metadata?.page as number) : undefined;
export function minimumPage(documents: CitationDocument[]): number | undefined {
	const pages = documents.map(snippetPage).filter((page): page is number => page !== undefined);
	return pages.length ? Math.min(...pages) + 1 : undefined;
}
export const fileContentUrl = (fileId: string | undefined, page?: number): string =>
	fileId ? `${WEBUI_API_BASE_URL}/files/${fileId}/content${page ? `#page=${page}` : ''}` : '';
export const truncate = (text: string, limit: number): string =>
	text.length > limit ? `${text.slice(0, limit).trimEnd()}…` : text;

export function calculatePercentage(distance: number) {
	if (typeof distance !== 'number') return null;
	if (distance < 0) return 0;
	if (distance > 1) return 100;
	return Math.round(distance * 10000) / 100;
}

export function getRelevanceColor(percentage: number) {
	if (percentage >= 80) return 'bg-green-200 dark:bg-green-800 text-green-800 dark:text-green-200';
	if (percentage >= 60)
		return 'bg-yellow-200 dark:bg-yellow-800 text-yellow-800 dark:text-yellow-200';
	if (percentage >= 40)
		return 'bg-orange-200 dark:bg-orange-800 text-orange-800 dark:text-orange-200';
	return 'bg-red-200 dark:bg-red-800 text-red-800 dark:text-red-200';
}
export const decodeString = (str: string) => {
	try {
		return decodeURIComponent(str);
	} catch {
		return str;
	}
};

export const getTextFragmentUrl = (doc: CitationDocument): string | null => {
	const { metadata, source, document: content } = doc ?? {};
	const { file_id, page } = metadata ?? {};
	const sourceUrl = source?.url;

	const baseUrl = file_id
		? `${WEBUI_API_BASE_URL}/files/${file_id}/content${page !== undefined ? `#page=${Number(page) + 1}` : ''}`
		: sourceUrl?.includes('http')
			? sourceUrl
			: null;

	if (!baseUrl || !content) return baseUrl;

	// Extract first and last words for text fragment, filtering out URLs and emojis
	const words = content
		.trim()
		.replace(/\s+/g, ' ')
		.split(' ')
		.filter((w: string) => w.length > 0 && !/https?:\/\/|[\u{1F300}-\u{1F9FF}]/u.test(w));

	if (words.length === 0) return baseUrl;

	const clean = (w: string) => w.replace(/[^\w]/g, '');
	const first = clean(words[0]);
	const last = clean(words.at(-1) ?? '');
	const fragment = words.length === 1 ? first : `${first},${last}`;

	return fragment ? `${baseUrl}#:~:text=${fragment}` : baseUrl;
};
