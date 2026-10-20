import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import { readable } from 'svelte/store';
import SearchSnippetNote from './SearchSnippetNote.svelte';
import { searchResultPage } from './searchSnippet';
import type { CitationDocument } from './citationDocuments';

const URL = 'https://www.example.com/products/widgets/alpha';
const snippet = (metadata: Record<string, unknown>): CitationDocument => ({
	source: { id: URL, name: URL, url: URL },
	document: 'Widget-A12 has 12 clamps ... Widget-B48',
	metadata: { source: URL, name: 'Alpha widgets', granularity: 'snippet', ...metadata }
});

describe('search result snippets', () => {
	it('names the page, its address and where to open it', () => {
		expect(searchResultPage(snippet({}))).toEqual({
			title: 'Alpha widgets',
			url: URL,
			address: 'example.com/products/widgets/alpha',
			file: false
		});
	});

	it('falls back to the address when the result has no title', () => {
		expect(searchResultPage(snippet({ name: '' }))?.title).toBe(
			'example.com/products/widgets/alpha'
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
		expect(html).toContain('Alpha widgets');
		expect(html).toContain('example.com/products/widgets/alpha');
		expect(html).toContain('Search result — snippet only, the page was not opened');
		expect(html).toContain(`href="${URL}"`);
	});

	it('names a file in the drive by its decoded address', () => {
		const url = 'https://tenant-my.sharepoint.com/personal/alex/Documents/Plans/Q3%20plan.pptx';
		const document: CitationDocument = {
			source: { id: 'hit-1', name: 'Q3 plan.pptx', url, provider: 'onedrive' },
			document: 'Q3 plan Widget launch in September',
			metadata: { source: 'hit-1', name: 'Q3 plan.pptx', granularity: 'file-snippet' }
		};
		expect(searchResultPage(document)).toEqual({
			title: 'Q3 plan.pptx',
			url,
			address: 'tenant-my.sharepoint.com/personal/alex/Documents/Plans/Q3 plan.pptx',
			file: true
		});
		const context = new Map([['i18n', readable({ t: (key: string) => key })]]);
		const html = render(SearchSnippetNote, { props: { document }, context }).body;
		expect(html).toContain('Search result in your files — snippet only, the file was not opened');
		expect(html).toContain('Open original file');
		expect(html).not.toContain('the page was not opened');
	});

	it('shows nothing for a passage of a read document', () => {
		const context = new Map([['i18n', readable({ t: (key: string) => key })]]);
		const document = snippet({ granularity: undefined });
		expect(render(SearchSnippetNote, { props: { document }, context }).body).not.toContain(
			'snippet only'
		);
	});
});
