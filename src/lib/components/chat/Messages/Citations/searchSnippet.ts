// [Gradient] A search result cited for its snippet alone: a web page's (`metadata.granularity === 'snippet'`),
// whose page the agent never fetched, or a file's in the user's drive (`'file-snippet'`), which it never opened.
import type { CitationDocument } from './citationDocuments';

export interface SearchResultPage {
	title: string;
	url: string;
	// The URL without its scheme and `www.`: the domain first, then the path; decoded for a file.
	address: string;
	// Whether it is a file in the user's drive rather than a web page.
	file: boolean;
}

const SNIPPETS = new Set(['snippet', 'file-snippet']);

export const isSearchSnippet = (document: CitationDocument | undefined): boolean =>
	SNIPPETS.has(document?.metadata?.granularity as string);

/** The page or file a search-result snippet came from; `null` for any other passage or a non-web address. */
export function searchResultPage(document: CitationDocument | undefined): SearchResultPage | null {
	if (!isSearchSnippet(document)) return null;
	const url = [document?.metadata?.source, document?.source?.url].find(
		(value): value is string => typeof value === 'string' && /^https?:\/\/[^/\s]/i.test(value)
	);
	if (!url) return null;
	const file = document?.metadata?.granularity === 'file-snippet';
	const bare = url.replace(/^https?:\/\/(www\.)?/i, '').replace(/\/$/, '');
	const address = file ? decoded(bare) : bare;
	const name = document?.metadata?.name;
	return { title: typeof name === 'string' && name.trim() ? name : address, url, address, file };
}

const decoded = (address: string): string => {
	try {
		return decodeURIComponent(address);
	} catch {
		return address;
	}
};
