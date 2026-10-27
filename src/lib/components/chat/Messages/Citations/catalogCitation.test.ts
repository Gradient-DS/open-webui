import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from 'svelte/server';
import { readable } from 'svelte/store';
import CitationHeader from './CitationHeader.svelte';
import CitationContent from './CitationContent.svelte';
import { createPreviewProbe, mergeCitationDocuments, probeFile } from './citationDocuments';
import { citationFileInfo } from './useCitationDocument';
import type { DisplayCitation } from './reduceSources';

const SOURCE_URL = 'https://publications.example/soil-and-water';
const sourceId = 'a'.repeat(64);

// A data-source hit: named by its title, no extension, linked to its publication page.
const catalog = (provider?: string): DisplayCitation => ({
	id: sourceId,
	source: {
		id: sourceId,
		name: 'Soil and water',
		url: SOURCE_URL,
		...(provider ? { provider } : {})
	},
	document: ['Clay holds water.'],
	metadata: [{ file_id: sourceId, name: 'Soil and water', source_url: SOURCE_URL, page: 2 }],
	distances: [undefined],
	n: 1
});
const context = () =>
	new Map([['i18n', readable({ t: (key: string) => key })]]) as Map<string, unknown>;

afterEach(() => vi.unstubAllGlobals());

describe('catalog document citations', () => {
	it('reads the served content type from the probe', async () => {
		const headers = new Headers({ 'content-type': 'text/plain; charset=utf-8' });
		vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200, headers }));
		expect(await probeFile('doc')).toEqual({ available: true, contentType: 'text/plain' });
	});

	it('forwards the content type of the latest check', async () => {
		const results: [boolean, string | undefined][] = [];
		const probe = createPreviewProbe(
			(available, contentType) => results.push([available, contentType]),
			async () => ({ available: true, contentType: 'application/pdf' })
		);
		await probe.check('doc');
		expect(results.at(-1)).toEqual([true, 'application/pdf']);
	});

	it('previews a titled document served as PDF and offers only text without an original', () => {
		const documents = mergeCitationDocuments(catalog());
		expect(citationFileInfo(catalog(), documents).isPreviewable).toBe(false);
		const pdf = citationFileInfo(catalog(), documents, 'application/pdf');
		expect(pdf.isPDF && pdf.isPreviewable).toBe(true);
		const named = { ...catalog(), metadata: [{ file_id: sourceId, name: 'report.pdf' }] };
		const text = citationFileInfo(named, mergeCitationDocuments(named), 'text/plain');
		expect(text.textOnly && !text.isPreviewable).toBe(true);
	});

	it('links the title icon to the source page, and the title to the file only with an original', () => {
		const citation = catalog();
		const props = {
			citation,
			mergedDocuments: mergeCitationDocuments(citation),
			externalUrl: SOURCE_URL
		};
		const textOnly = render(CitationHeader, {
			props: { ...props, textOnly: true },
			context: context()
		}).body;
		expect(textOnly).not.toContain(`/files/${sourceId}/content`);
		expect(textOnly).toContain(`href="${SOURCE_URL}"`);
		expect(textOnly).toContain('Open original page');
		const original = render(CitationHeader, { props, context: context() }).body;
		expect(original).toContain(`/files/${sourceId}/content#page=3`);
		expect(original).toContain(`href="${SOURCE_URL}"`);
	});

	it('never turns the text label into a link, for any citation kind', () => {
		vi.stubGlobal('requestAnimationFrame', () => 0);
		vi.stubGlobal('cancelAnimationFrame', () => undefined);
		const label = (citation: DisplayCitation) =>
			render(CitationContent, {
				props: { mergedDocuments: mergeCitationDocuments(citation), showRelevance: false },
				context: context()
			}).body;
		expect(label(catalog())).not.toContain('/content#');
		expect(label(catalog('onedrive'))).not.toContain('<a');
		expect(label(catalog())).not.toContain('<a');
	});
});
