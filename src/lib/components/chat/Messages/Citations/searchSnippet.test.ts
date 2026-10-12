import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import { readable } from 'svelte/store';
import SearchSnippetNote from './SearchSnippetNote.svelte';
import { searchResultPage } from './searchSnippet';
import type { CitationDocument } from './citationDocuments';

const URL = 'https://www.quantware.com/product/processors/d-line';
const snippet = (metadata: Record<string, unknown>): CitationDocument => ({
	source: { id: URL, name: URL, url: URL },
	document: 'Contralto-D21 expands to 21 qubits ... Tenor-D64',
	metadata: { source: URL, name: 'D-line QPUs', granularity: 'snippet', ...metadata }
});

describe('search result snippets', () => {
	it('names the page, its address and where to open it', () => {
		expect(searchResultPage(snippet({}))).toEqual({
			title: 'D-line QPUs',
			url: URL,
			address: 'quantware.com/product/processors/d-line'
		});
	});

	it('falls back to the address when the result has no title', () => {
		expect(searchResultPage(snippet({ name: '' }))?.title).toBe(
			'quantware.com/product/processors/d-line'
		);
	});

	it.each([
		{ granularity: undefined },
		{ granularity: 'document' },
		{ source: 'javascript:alert(1)' }
	])('is no search result page for %o', (metadata) => {
		const document = snippet(metadata);
		document.source = { id: 'x', name: 'x' };
		expect(searchResultPage(document)).toBeNull();
	});

	it('says the page was not opened and links to it', () => {
		const context = new Map([['i18n', readable({ t: (key: string) => key })]]);
		const html = render(SearchSnippetNote, { props: { document: snippet({}) }, context }).body;
		expect(html).toContain('D-line QPUs');
		expect(html).toContain('quantware.com/product/processors/d-line');
		expect(html).toContain('Search result — snippet only, the page was not opened');
		expect(html).toContain(`href="${URL}"`);
	});

	it('shows nothing for a passage of a read document', () => {
		const context = new Map([['i18n', readable({ t: (key: string) => key })]]);
		const document = snippet({ granularity: undefined });
		expect(render(SearchSnippetNote, { props: { document }, context }).body).not.toContain(
			'snippet only'
		);
	});
});
