import { describe, expect, it, vi, afterEach } from 'vitest';
import { mergeCitationDocuments, probeFileAvailable } from './citationDocuments';
import {
	minimumPage,
	rectsFromSnippet,
	resolveExternalUrl,
	citationFileInfo
} from './useCitationDocument';
import type { DisplayCitation } from './reduceSources';

const citation: DisplayCitation = {
	id: 'file',
	source: { name: 'report.PDF', url: 'https://original.example' },
	document: ['first', 'second'],
	metadata: [
		{ file_id: 'file', page: 8 },
		{ file_id: 'file', page: 2 }
	],
	distances: [0.4, 0.9]
};
afterEach(() => vi.unstubAllGlobals());
describe('shared citation documents', () => {
	it('sorts complete scores while keeping text and page metadata aligned', () => {
		const documents = mergeCitationDocuments(citation);
		expect(documents.map((doc) => [doc.document, doc.metadata?.page])).toEqual([
			['second', 2],
			['first', 8]
		]);
		expect(citation.document).toEqual(['first', 'second']);
		expect(minimumPage(documents)).toBe(3);
		expect(citationFileInfo(citation, documents).isPreviewable).toBe(true);
	});
	it('preserves input order when a whole-document citation has no score', () => {
		expect(
			mergeCitationDocuments({ ...citation, distances: [undefined, 0.9] }).map(
				(doc) => doc.document
			)
		).toEqual(['first', 'second']);
	});
	it('keeps bbox page conversion and suppresses whole-document highlights', () => {
		const snippet = {
			source: {},
			document: 'passage',
			metadata: { page: 2, bboxes: [{ x0: 1, y0: 2, x1: 3, y1: 4 }] }
		};
		expect(rectsFromSnippet(snippet)?.[0].page).toBe(3);
		expect(
			rectsFromSnippet({ ...snippet, metadata: { ...snippet.metadata, granularity: 'document' } })
		).toBeNull();
	});
	it('prefers source URL and falls back to chunk provenance', () => {
		const docs = [
			{ source: {}, document: '', metadata: { source_url: 'https://fallback.example' } }
		];
		expect(resolveExternalUrl(citation, docs)).toBe('https://original.example');
		expect(resolveExternalUrl(null, docs)).toBe('https://fallback.example');
	});
	it('authenticates the availability probe', async () => {
		vi.stubGlobal('localStorage', { getItem: () => 'test-token' });
		const fetch = vi.fn().mockResolvedValue({ ok: true });
		vi.stubGlobal('fetch', fetch);
		expect(await probeFileAvailable('file')).toBe(true);
		expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/files/file/content'), {
			method: 'HEAD',
			headers: { authorization: 'Bearer test-token' }
		});
	});
	it('falls back to content on failed probes', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValueOnce({ ok: false }).mockRejectedValueOnce(new Error('offline'))
		);
		expect(await probeFileAvailable('file')).toBe(false);
		expect(await probeFileAvailable('file')).toBe(false);
	});
});
