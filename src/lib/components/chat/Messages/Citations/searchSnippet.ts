// [Gradient] A search result cited for its snippet alone: a web page's (`metadata.granularity === 'snippet'`),
// whose page the agent never fetched, a file's in the user's drive (`'file-snippet'`), which it never opened, or
// a council document's (`'council-snippet'`), which it never read.
import type { CitationDocument } from './citationDocuments';

export type SearchResultKind = 'web' | 'file' | 'council';

export interface SearchResultPage {
	title: string;
	// Where the original opens; `null` for a council result the source links nothing for (a meeting).
	url: string | null;
	// The URL without its scheme and `www.`: the domain first, then the path; decoded for a file.
	address: string;
	kind: SearchResultKind;
	// Whether it is a file in the user's drive rather than a web page.
	file: boolean;
	// A council result's rendered image of the PDF page the fragment is on.
	preview: string | null;
}

export interface SnippetSegment {
	text: string;
	marked: boolean;
}

const KINDS: Record<string, SearchResultKind> = {
	snippet: 'web',
	'file-snippet': 'file',
	'council-snippet': 'council'
};

const WEB_URL = /^https?:\/\/[^/\s]/i;

export const isSearchSnippet = (document: CitationDocument | undefined): boolean =>
	(document?.metadata?.granularity as string) in KINDS;

/** The page or file a search-result snippet came from; `null` for any other passage or a non-web address. */
export function searchResultPage(document: CitationDocument | undefined): SearchResultPage | null {
	if (!isSearchSnippet(document)) return null;
	const kind = KINDS[document?.metadata?.granularity as string];
	const url =
		[document?.metadata?.source, document?.source?.url].find(
			(value): value is string => typeof value === 'string' && WEB_URL.test(value)
		) ?? null;
	if (!url && kind !== 'council') return null;
	const file = kind === 'file';
	const bare = (url ?? '').replace(/^https?:\/\/(www\.)?/i, '').replace(/\/$/, '');
	const address = file ? decoded(bare) : bare;
	const name = document?.metadata?.name;
	const preview = document?.metadata?.preview_url;
	return {
		title: typeof name === 'string' && name.trim() ? name : address,
		url,
		address,
		kind,
		file,
		preview:
			kind === 'council' && typeof preview === 'string' && /^https:\/\//i.test(preview)
				? preview
				: null
	};
}

/** The snippet as segments, the source's matched words marked; well-formed, in-range `[start, end)` ranges only. */
export function snippetSegments(text: string, highlights: unknown): SnippetSegment[] {
	const ranges = (Array.isArray(highlights) ? highlights : [])
		.filter(
			(range): range is [number, number] =>
				Array.isArray(range) &&
				Number.isInteger(range[0]) &&
				Number.isInteger(range[1]) &&
				range[0] >= 0 &&
				range[0] < range[1] &&
				range[1] <= text.length
		)
		.sort((a, b) => a[0] - b[0]);
	const segments: SnippetSegment[] = [];
	let at = 0;
	for (const [start, end] of ranges) {
		if (start < at) continue;
		if (start > at) segments.push({ text: text.slice(at, start), marked: false });
		segments.push({ text: text.slice(start, end), marked: true });
		at = end;
	}
	if (at < text.length) segments.push({ text: text.slice(at), marked: false });
	return segments;
}

const decoded = (address: string): string => {
	try {
		return decodeURIComponent(address);
	} catch {
		return address;
	}
};
