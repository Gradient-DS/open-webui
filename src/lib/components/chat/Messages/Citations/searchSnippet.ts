// [Gradient] A web search result cited for its snippet alone (`metadata.granularity === 'snippet'`):
// the agent read the search engine's fragment, never the page.
import type { CitationDocument } from './citationDocuments';

export interface SearchResultPage {
	title: string;
	url: string;
	// The URL without its scheme and `www.`: the domain first, then the path.
	address: string;
}

export const isSearchSnippet = (document: CitationDocument | undefined): boolean =>
	document?.metadata?.granularity === 'snippet';

/** The page a search-result snippet came from; `null` for any other passage or a non-web address. */
export function searchResultPage(document: CitationDocument | undefined): SearchResultPage | null {
	if (!isSearchSnippet(document)) return null;
	const url = [document?.metadata?.source, document?.source?.url].find(
		(value): value is string => typeof value === 'string' && /^https?:\/\/[^/\s]/i.test(value)
	);
	if (!url) return null;
	const address = url.replace(/^https?:\/\/(www\.)?/i, '').replace(/\/$/, '');
	const name = document?.metadata?.name;
	return { title: typeof name === 'string' && name.trim() ? name : address, url, address };
}
